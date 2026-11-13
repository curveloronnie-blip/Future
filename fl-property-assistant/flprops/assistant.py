"""Conversational assistant over the local records database, powered by Claude.

Claude gets tools that query the local database (filled from official bulk files and open-data
APIs) and that produce official portal links. Requires ANTHROPIC_API_KEY (or `ant auth login`).
"""
from __future__ import annotations

import json
import sqlite3

import anthropic

from . import fetch, links, search
from .db import EVENT_TYPES

MODEL = "claude-opus-5-5"

SYSTEM = f"""You are a Florida real-estate public-records research assistant.

You answer from a local database built from the Florida Department of Revenue statewide parcel
roll (NAL) and from county/city open-data feeds and clerk/auction exports the user has imported.
Use the tools; never invent owners, values, case numbers or dates. If the database lacks
something, say so and give the official portal links from county_resources so the user can look
it up, and say which import (NAL file for that county, or an event CSV) would fill the gap.

Record event types: {", ".join(EVENT_TYPES)}.

Some clerk and auction sites require a person to pass a CAPTCHA or accept a disclaimer. Do not
offer to bypass those; give the link instead. Note the data's freshness when relevant (NAL files
are annual with mid-year updates; events are as fresh as their last import).
Distress scores are heuristics, not facts about a person's circumstances; present them as such.
Keep answers tight: lead with the answer, then a short table or list."""

TOOLS = [
    {
        "name": "search_properties",
        "description": "Find Florida parcels by street address, owner name, or parcel/folio number. "
                       "Returns owner, mailing address, values, homestead, last sale, and a distress score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Address, owner name, or parcel/folio number."},
                "county": {"type": "string", "description": "Florida county name, optional."},
                "zip_code": {"type": "string", "description": "5-digit ZIP of the property, optional."},
                "limit": {"type": "integer", "description": "Max results (default 15)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_property",
        "description": "Full record for one parcel: owner, values, sales, all recorded events "
                       "(liens, lis pendens, foreclosure, probate, violations...), distress breakdown, and portal links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "parcel_id": {"type": "string"},
                "county": {"type": "string"},
            },
            "required": ["parcel_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_distressed",
        "description": "Rank distressed properties in an area by distress score. Optionally require "
                       f"specific event types ({', '.join(EVENT_TYPES)}).",
        "input_schema": {
            "type": "object",
            "properties": {
                "county": {"type": "string"},
                "zip_code": {"type": "string"},
                "min_score": {"type": "integer", "description": "0-100, default 25."},
                "require": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "description": "default 25"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "county_resources",
        "description": "Official portal links for a Florida county: property appraiser, clerk official "
                       "records and court records, tax collector, code enforcement, foreclosure and tax deed auctions.",
        "input_schema": {
            "type": "object",
            "properties": {"county": {"type": "string"}},
            "required": ["county"],
            "additionalProperties": False,
        },
    },
    {
        "name": "database_status",
        "description": "What's loaded: parcel counts by county, event counts by type, latest imports.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "refresh_live_sources",
        "description": "Pull the latest rows from the open-data feeds configured in config/sources.json.",
        "input_schema": {
            "type": "object",
            "properties": {"source": {"type": "string", "description": "Name of one source; omit for all."}},
            "additionalProperties": False,
        },
    },
]

_SLIM = ("county", "parcel_id", "owner_name", "owner_addr1", "owner_city", "owner_state", "owner_zip",
         "site_addr", "site_city", "site_zip", "just_value", "homestead", "year_built", "living_sqft",
         "last_sale_price", "last_sale_year")


def _slim(p: dict) -> dict:
    out = {k: p.get(k) for k in _SLIM}
    out["distress_score"] = p["distress"]["score"]
    out["distress_reasons"] = [s["reason"] for s in p["distress"]["signals"]]
    if "event_types" in p:
        out["event_types"] = p["event_types"]
    return out


def run_tool(conn: sqlite3.Connection, name: str, args: dict):
    if name == "search_properties":
        return [_slim(p) for p in search.search(conn, args["query"], args.get("county"),
                                                args.get("zip_code"), min(int(args.get("limit") or 15), 50))]
    if name == "get_property":
        p = search.get_property(conn, args["parcel_id"], args.get("county"))
        return p or {"error": "parcel not found in local database"}
    if name == "list_distressed":
        rows = search.distressed(conn, args.get("county"), args.get("zip_code"),
                                 int(args.get("min_score") or 25), args.get("require"),
                                 min(int(args.get("limit") or 25), 100))
        return [_slim(p) for p in rows]
    if name == "county_resources":
        return links.county_links(args["county"])
    if name == "database_status":
        return search.stats(conn)
    if name == "refresh_live_sources":
        return fetch.refresh_all(conn, args.get("source"))
    raise ValueError(f"unknown tool {name}")


class Assistant:
    def __init__(self, conn: sqlite3.Connection, client: anthropic.Anthropic | None = None):
        self.conn = conn
        self.client = client or anthropic.Anthropic()
        self.messages: list = []  # append-only conversation history

    def ask(self, text: str, max_steps: int = 12) -> str:
        self.messages.append({"role": "user", "content": text})
        for _ in range(max_steps):
            resp = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                tools=TOOLS,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                return "The model declined this request."
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                try:
                    out = run_tool(self.conn, b.name, b.input if isinstance(b.input, dict) else json.loads(b.input))
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(out, default=str)[:60000]})
                except Exception as e:
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": f"Error: {e}", "is_error": True})
            self.messages.append({"role": "user", "content": results})
        return "Stopped after too many tool steps; try a narrower question."
