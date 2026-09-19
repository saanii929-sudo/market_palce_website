FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
# daphne (ASGI) rather than gunicorn (WSGI) - the chat app's WebSocket
# connections need an ASGI server; daphne serves regular HTTP requests fine too.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "sports_shop.asgi:application"]
