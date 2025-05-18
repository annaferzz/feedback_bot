FROM python:3.13-rc-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/temp_photos && \
    touch /app/feedback_bot.log

CMD ["python", "./src/feedback_bot.py"]