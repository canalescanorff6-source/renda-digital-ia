# Correção v22 — rota segura de vídeo no RunSite

Esta atualização garante que a geração de vídeo funcione por uma rota HTML segura:

- `/produtos/<id>/gerar-video` — gera e mostra a tela de prévia.
- `/produtos/<id>/baixar-video` — baixa o MP4.
- `/produtos/<id>/assistir-video` — abre o MP4 dentro da página.
- `/produtos/<id>/video-venda.mp4` — mantida apenas para compatibilidade.

Depois de aplicar no Git e fazer deploy, entre pelo sistema:

`Meus produtos -> Produto -> Vídeo MP4 temático`

Não digite o link manualmente antes de confirmar que o deploy novo ficou LIVE.

Para conferir se a rota entrou no app.py:

`findstr /C:"gerar-video" app.py`

Se aparecer, faça commit e push.
