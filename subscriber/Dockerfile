FROM python:3.12-slim

WORKDIR /app

COPY subscriber/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY subscriber /app/subscriber

EXPOSE 8080

CMD ["python", "-m", "subscriber.app"]

