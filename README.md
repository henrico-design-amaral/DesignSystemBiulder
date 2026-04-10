# Capturar — Website Downloader

Ferramenta web para baixar réplicas completas de qualquer site, incluindo conteúdo renderizado por JavaScript.

![screenshot](https://i.imgur.com/placeholder.png)

## Como funciona

1. **Captura** — Playwright abre um Chromium headless e renderiza a página com JS real
2. **Lazy loading** — Rola a página automaticamente para forçar o carregamento de imagens
3. **Processamento** — BeautifulSoup reescreve todas as URLs para caminhos locais
4. **Limpeza** — Remove scripts de hydration (Next.js, Nuxt, Gatsby) e smooth scroll libs que quebram offline
5. **Empacotamento** — Cria um ZIP com tudo: HTML, CSS, JS, imagens, fontes
6. **Progresso** — Logs em tempo real via Server-Sent Events

## Stack

- **Backend:** Python + Flask + Playwright + BeautifulSoup
- **Frontend:** HTML/CSS/JS puro (sem frameworks)
- **Deploy:** Docker, Render, Railway

## Rodar localmente

### Com uv (recomendado)

```bash
# Clonar
git clone https://github.com/seu-usuario/capturar
cd capturar

# Instalar dependências
uv sync

# Instalar Chromium
uv run playwright install chromium

# Rodar
uv run python app.py
```

Acesse: http://localhost:5001

### Com pip

```bash
pip install -r requirements.txt
playwright install chromium
python app.py
```

### Com Docker

```bash
docker build -t capturar .
docker run -p 5001:5001 capturar
```

## Deploy em produção

### Render

1. Fork este repositório
2. Crie um novo serviço em [render.com](https://render.com) → **New Web Service**
3. Conecte o repositório
4. Render detecta o `render.yaml` automaticamente
5. Deploy

### Railway

1. Fork este repositório
2. Crie um novo projeto em [railway.app](https://railway.app)
3. **Deploy from GitHub repo** → selecione o repositório
4. Railway detecta o `railway.toml` automaticamente
5. Deploy

## Estrutura do projeto

```
capturar/
├── app.py              # Servidor Flask — rotas e SSE
├── downloader.py       # Lógica de captura e processamento
├── templates/
│   └── index.html      # Interface do usuário
├── downloads/          # ZIPs temporários (auto-limpeza após 10min)
├── Dockerfile
├── render.yaml
├── railway.toml
├── pyproject.toml
└── requirements.txt
```

## Limitações

- Sites com autenticação não são suportados
- SPAs com roteamento dinâmico capturam apenas a página raiz
- Sites com CSP muito restrito podem ter assets faltando

## Licença

Uso pessoal e educacional.

## Design System Generator

Após cada download, o sistema gera automaticamente um `design-system.html` extraído do HTML capturado.

O arquivo documenta:
- **Hero** — clone exato do original
- **Typography** — escala tipográfica real do DOM
- **Colors & Surfaces** — cores extraídas de classes Tailwind arbitrárias e camadas glass
- **Components** — nav, botões, badges, cards retirados literalmente do markup
- **Layout** — containers, grids e ritmo de espaçamento
- **Motion** — keyframes e classes de transição do CSS original
- **Icons** — conjunto de ícones com markup exato (se presente)

O botão **Design System** aparece ao lado do **Baixar ZIP** após a captura ser concluída. O arquivo abre em nova aba e expira junto com o ZIP (10 minutos).

### Regras de extração

- Nunca reinventa classes
- Nunca cria componentes ausentes do HTML fonte  
- Sempre referencia os assets originais (CSS, JS, fontes)
- Nunca normaliza markup nem interpola estilos
