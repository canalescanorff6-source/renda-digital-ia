# Correção v25 — vídeo com rota curta e app.run corrigido

Esta versão corrige o problema onde `/produtos/1/gerar-video` caía na página 404 do RunSite.

Mudanças:
- removeu o `app.run()` duplicado que aparecia antes das rotas novas;
- adicionou rota curta e segura: `/video-produto/1`;
- o botão do produto agora usa `/video-produto/<id>`;
- mantém compatibilidade com `/produtos/<id>/gerar-video`;
- vídeo continua sendo gerado em segundo plano e mais leve.

Teste principal:
`https://renda-digital-ia.runsite.app/video-produto/1`
