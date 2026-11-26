FROM python:3.13-slim

WORKDIR /app
COPY vendor /tmp/vendor
RUN pip install --no-cache-dir /tmp/vendor/*.whl && rm -rf /tmp/vendor
COPY app.py /app/app.py
COPY config /app/config
EXPOSE 2101
CMD ["python", "app.py"]
