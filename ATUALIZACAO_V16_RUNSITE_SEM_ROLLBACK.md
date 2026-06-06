# Atualização v16 - RunSite sem rollback por startup lento

Esta atualização remove o teste de importação do `start.sh` antes de iniciar o Gunicorn.

## Por quê?

No RunSite, o container precisa abrir a porta rápido. O script antigo fazia:

```bash
python - <<'PY'
import app
PY
```

Esse teste deixava o início mais lento. Quando o RunSite entendia que o app demorou para ficar pronto, ele encerrava o container e voltava para a versão anterior, aparecendo `Rolling back`.

## Arquivos alterados

- `start.sh`
- `Procfile`
- `Dockerfile`

## Start command recomendado

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 180
```

Ou deixe o Dockerfile usar:

```bash
./start.sh
```

## Observação importante

Se você usa SQLite em `/tmp`, os produtos podem sumir quando o app reinicia ou quando há novo deploy. Para vender de verdade, configure um banco/volume persistente depois que a versão estiver estável.
