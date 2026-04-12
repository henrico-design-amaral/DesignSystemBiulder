FROM python:3.11

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

RUN playwright install --with-deps chromium

CMD ["python", "app.py"]
