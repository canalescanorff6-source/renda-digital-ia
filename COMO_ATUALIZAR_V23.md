# Como atualizar para a v23

## 1. Aplicar no Git

Extraia o patch dentro da pasta do projeto:

```bash
cd C:\Users\Administrator\Documents\GitHub\renda-digital-ia
git add .
git commit -m "checkup final v23 corrige rotas videos e textos multinicho"
git push
```

## 2. RunSite

Mantenha o start command:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 180
```

Variáveis recomendadas:

```text
SECRET_KEY=RDI_PRO_2026_8fK9zP2qL7vM4xA1eN6
ADMIN_EMAIL=thiago01268230@gmail.com
ADMIN_PASSWORD=TROQUE_POR_UMA_SENHA_FORTE
FLASK_ENV=production
WEB_CONCURRENCY=1
GUNICORN_TIMEOUT=180
ROBOT_SECRET=RoboRenda2026Luiz9xK72p
PUBLIC_BASE_URL=https://renda-digital-ia.runsite.app
```

Não coloque `APP_NAME`.

## 3. Testar depois do deploy

Abra:

```text
https://renda-digital-ia.runsite.app/healthz
https://renda-digital-ia.runsite.app/diagnostico
https://renda-digital-ia.runsite.app/biblioteca-premium
```

Depois crie um produto e teste:

```text
Meus produtos → produto criado → Vídeo MP4 temático
```

Rotas seguras de vídeo:

```text
/produtos/1/gerar-video
/produtos/1/video
/produtos/1/video-venda
/produtos/1/baixar-video
/produtos/1/assistir-video
```
