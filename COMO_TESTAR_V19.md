# Como testar a v19 após o deploy

1. Abra `/healthz`.
2. Faça login.
3. Abra `/diagnostico`.
4. Abra `Biblioteca premium`.
5. Clique em um produto para criar.
6. Na página do produto, teste:
   - baixar PDF do produto;
   - baixar texto editável;
   - gerar vídeo;
   - abrir página pública;
   - abrir Robô comercial.

Variáveis recomendadas no RunSite:

```text
SECRET_KEY=RDI_PRO_2026_8fK9zP2qL7vM4xA1eN6
ADMIN_EMAIL=thiago01268230@gmail.com
ADMIN_PASSWORD=TROQUE_POR_UMA_SENHA_NOVA_FORTE
FLASK_ENV=production
WEB_CONCURRENCY=1
GUNICORN_TIMEOUT=180
ROBOT_SECRET=RoboRenda2026Luiz9xK72p
PUBLIC_BASE_URL=https://renda-digital-ia.runsite.app
```

Start command:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 180
```
