FROM python:3.9.15-slim

WORKDIR /root

COPY . .

RUN pip install -r requirements.txt

CMD ["python", "app.py"]