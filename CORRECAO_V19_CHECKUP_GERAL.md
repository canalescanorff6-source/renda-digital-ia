# Correção v19 — Checkup geral de erros

Esta atualização foi feita para corrigir falhas reais encontradas no uso do sistema no RunSite.

## Corrigido

- Erro 500 ao criar produto pela Biblioteca Premium.
- Slugs/links públicos com acentos e caracteres difíceis.
- Downloads com nomes alternativos: `produto`, `venda`, `posts`, `prompts`, `bncc` e `amostra`.
- Edição de produto apagando textos premium quando algum formulário parcial não enviava os campos de texto.
- Mensagens de erro 404/500 mais claras em vez de tela branca simples.
- Adicionada rota `/diagnostico` para testar banco, pastas, PDF e vídeo.
- Mantido start seguro para RunSite com 1 worker.
- Mantidos textos premium, visual temático, vídeos MP4 e robô comercial.

## Testes realizados

- Importação do app: OK
- `/healthz`: OK
- Login: OK
- Dashboard: OK
- Biblioteca premium: OK
- Criação dos 8 produtos da biblioteca: OK
- Página do produto: OK
- Página pública: OK
- Download Markdown: OK
- Download PDF: OK
- Pacote cliente ZIP: OK
- Pacote lançamento ZIP: OK
- Pacote profissional ZIP: OK
- Campanhas e vídeos: OK
- Vídeo MP4: OK
- Robô comercial: OK
- Cron protegido por ROBOT_SECRET: OK
- Diagnóstico interno: OK

## Rota de diagnóstico

Depois de atualizar, entre no site e abra:

```text
/diagnostico
```

Ela mostra se banco, pasta de exportação, PDF e vídeo estão prontos.

## Observação importante

Se o RunSite estiver usando banco SQLite dentro de `/tmp`, os produtos podem sumir após redeploy ou reinício. Para venda real constante, configure storage persistente ou banco externo quando possível.
