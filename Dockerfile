FROM python:3.12-slim

# Dependências do sistema para o Chromium
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    fonts-liberation libappindicator3-1 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala Playwright + Chromium dentro do container
# PLAYWRIGHT_BROWSERS_PATH garante que fica em local conhecido
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium --with-deps

# Copia código
COPY . .

# Pasta de downloads temporários
RUN mkdir -p downloads

# Railway injeta PORT dinamicamente
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 180 --worker-class sync --log-level debug"]
