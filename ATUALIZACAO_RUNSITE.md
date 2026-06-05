# Atualização RunSite v9.4

Correção focada no deploy que entrava em CrashLoopBackOff.

Arquivos alterados:
- Dockerfile
- start.sh
- Procfile

O que mudou:
- Removido `ENV PORT=5000` do Dockerfile.
- O app agora usa a porta definida pelo RunSite (`$PORT`).
- Se o RunSite não enviar `$PORT`, usa 8080.
- Banco e exportações usam `/tmp/renda_digital_ia` por padrão para evitar erro de permissão em hospedagem.
- O start imprime logs de diagnóstico antes de iniciar o Gunicorn.

Variáveis recomendadas no RunSite:

SECRET_KEY=sua_chave_grande
ADMIN_EMAIL=thiago01268230@gmail.com
ADMIN_PASSWORD=sua_senha_nova
FLASK_ENV=production

Não crie APP_NAME.
