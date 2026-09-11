FROM apify/actor-python:3.12
WORKDIR /usr/src/app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY .actor/ ./.actor/
COPY src/ ./src/
CMD ["python", "-m", "src"]
