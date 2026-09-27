FROM python:3.12-slim

RUN pip install --no-cache-dir mlflow==2.17.2

EXPOSE 5000

CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000", \
     "--backend-store-uri", "/mlflow/store", "--artifacts-destination", "/mlflow/artifacts"]
