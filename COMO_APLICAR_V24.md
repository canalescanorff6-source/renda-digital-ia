# Como aplicar a v24 no Git

Na pasta do projeto:

```bash
cd C:\Users\Administrator\Documents\GitHub\renda-digital-ia
git add .
git commit -m "corrige video em segundo plano no runsite"
git push
```

No RunSite, aguarde o deploy ficar `Running` no commit novo.

Depois teste:

```text
https://renda-digital-ia.runsite.app/healthz
https://renda-digital-ia.runsite.app/diagnostico
```

Para o vídeo, entre pelo botão do produto. Não digite o link manualmente.
