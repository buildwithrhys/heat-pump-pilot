FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir -p /app/data
ENV PREDHEAT_API_HOST=0.0.0.0 PREDHEAT_DATABASE_URL=sqlite:////app/data/predheat.sqlite
EXPOSE 8750
CMD ["predheat", "serve"]
