FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FLPROPS_DB=/data/flprops.db
VOLUME ["/data"]
EXPOSE 8765
# Binds to all interfaces inside the container; publish it only to localhost or put it behind a login.
CMD ["python", "-c", "from flprops.web import create_app; create_app().run(host='0.0.0.0', port=8765)"]
