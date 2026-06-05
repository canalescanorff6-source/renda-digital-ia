# Atualização RunSite

Correções aplicadas nesta versão:

- Corrige CrashLoopBackOff causado pela criação simultânea do usuário admin no SQLite.
- Ajusta o start.sh para usar 1 worker por padrão, mais seguro com SQLite em hospedagem simples.
- Mantém compatibilidade se a hospedagem usar mais de 1 worker, usando INSERT OR IGNORE no admin.
- Limpa termos visíveis pouco profissionais na interface.
- Adiciona .gitignore e .dockerignore para evitar subir banco, cache, .env e arquivos gerados.

## Variáveis no RunSite

Use apenas:

```text
SECRET_KEY=sua_chave_grande
ADMIN_EMAIL=seu_email
ADMIN_PASSWORD=sua_senha
FLASK_ENV=production
```

Não crie a variável APP_NAME no RunSite.

## Start Command

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 180
```

Ou:

```bash
bash start.sh
```

## Testes após deploy

Abra primeiro:

```text
/healthz
```

Depois:

```text
/login
```
