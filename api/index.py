"""
Ponto de entrada do EMC Monitor na Vercel.

A Vercel executa cada requisição como uma "função serverless": um processo que
nasce, responde e morre. Isso tem duas consequências importantes para este app:

1. Não existe processo rodando em segundo plano. O agendador (APScheduler) que
   existia no Render nunca dispararia aqui. Por isso ele é desligado via a
   variável EMC_SEM_AGENDADOR. Quem executa a busca diária é o GitHub Actions
   (.github/workflows/busca_dou.yml) — que é o lugar certo, porque a busca leva
   cerca de 25 minutos e a Vercel corta funções em poucos segundos.

2. O diretório de trabalho não é necessariamente a raiz do projeto. Como o app
   monta os templates e os arquivos estáticos por caminho relativo
   ("app/templates", "static"), é preciso fixar o diretório antes de importar.
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Permite "import app.main" e faz os caminhos relativos resolverem certo.
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)

# Desliga o agendador interno: na Vercel ele não teria como funcionar.
os.environ.setdefault("EMC_SEM_AGENDADOR", "1")

from app.main import app  # noqa: E402

# A Vercel procura por uma variável chamada `app` ou `handler`.
handler = app
