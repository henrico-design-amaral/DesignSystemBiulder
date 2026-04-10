# Capturar

Ferramenta web para capturar páginas públicas e gerar um pacote offline com `index.html`, assets locais e, quando disponível, `design-system.html`.

## O que esta versão faz de verdade

- Renderiza a página com Playwright + Chromium.
- Rola a página para tentar acionar lazy loading.
- Reescreve referências do HTML e `url(...)` de CSS inline e CSS baixado.
- Empacota a captura em ZIP.
- Gera um `design-system.html` por fallback local e, opcionalmente, por API externa quando `ANTHROPIC_API_KEY` estiver configurada.

## Limites reais

- Não suporta login, sessão autenticada ou navegação multi-step.
- Não é um espelho perfeito de qualquer SPA complexa.
- Alguns sites podem manter dependências remotas ou quebrar interações avançadas offline.
- Destinos privados, locais ou reservados são bloqueados por segurança.

## Stack

- Backend: Flask + Gunicorn
- Captura: Playwright + Chromium
- Parsing: BeautifulSoup
- Deploy: Docker + Railway

## Rodar localmente

### Com `uv`

```bash
git clone <seu-repo>
cd capturar
uv sync
uv run playwright install chromium
uv run python app.py
```

Acesse: `http://localhost:8080`

### Com `pip`

```bash
pip install -r requirements.txt
playwright install chromium
python app.py
```

### Com Docker

```bash
docker build -t capturar .
docker run --rm -p 8080:8080 capturar
```

## Variáveis de ambiente

Copie `.env.example` como base. As mais importantes:

```bash
PORT=8080
DOWNLOAD_RETENTION_SECONDS=600
MAX_CONCURRENT_JOBS=2
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_MAX_REQUESTS=5
MAX_ASSET_BYTES=26214400
MAX_CAPTURED_RESOURCES=600
APP_API_TOKEN=
ALLOWED_DOMAIN_SUFFIXES=
DENIED_DOMAIN_SUFFIXES=localhost,local,internal,test,example,invalid
DESIGN_SYSTEM_MODE=auto
ANTHROPIC_API_KEY=
```

### Recomendações para produção

- Defina `APP_API_TOKEN` se o serviço não for público.
- Use `ALLOWED_DOMAIN_SUFFIXES` se quiser restringir a captura a domínios confiáveis.
- Configure `ANTHROPIC_API_KEY` apenas se quiser tentar geração externa do design system.
- Mantenha `DESIGN_SYSTEM_MODE=fallback` se quiser previsibilidade e custo zero de API.

## Deploy no Railway

### Via GitHub

1. Suba este projeto em um repositório Git.
2. No Railway, crie um novo serviço apontando para o repositório.
3. O Railway usará o `Dockerfile` deste projeto.
4. Configure pelo menos estas variáveis:
   - `PORT=8080`
   - `APP_API_TOKEN` opcional
   - `ANTHROPIC_API_KEY` opcional
5. Confirme que o healthcheck está em `/healthz`.

### Via CLI

```bash
railway up
```

## Estrutura do projeto

```text
capturar/
├── app.py
├── downloader.py
├── design_system_generator.py
├── templates/
│   └── index.html
├── downloads/
├── Dockerfile
├── railway.toml
├── requirements.txt
├── .env.example
└── .dockerignore
```

## Melhorias aplicadas nesta revisão

- bloqueio de SSRF básico para destinos privados/locais
- rate limit em memória
- limitação de concorrência
- limpeza automática de arquivos expirados
- assets temporários em disco em vez de RAM
- reescrita de `url(...)` também nos CSS baixados
- rota dedicada para abrir `design-system.html`
- frontend alinhado com os metadados reais do backend
- documentação alinhada com o comportamento atual

## Healthcheck

`GET /healthz`

Resposta esperada:

```json
{"status":"ok","active_capacity":2,"retention_seconds":600}
```
