FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg git curl && apt-get clean

WORKDIR /app

COPY . .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

EXPOSE 5000

CMD ["python", "main.py"]
