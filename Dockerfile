FROM python:3.13-slim

WORKDIR /app
COPY app.py /app/app.py
EXPOSE 2101
CMD ["python", "app.py"]
