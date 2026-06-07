# v26 — Vídeo estável para RunSite

Esta versão remove a geração pesada de MP4 dentro do RunSite.

O sistema agora gera um **Kit de vídeo para CapCut/Canva** com:

- roteiro profissional;
- cenas organizadas;
- legendas;
- storyboard CSV;
- prompt para vídeo com IA;
- instruções para CapCut e Canva;
- checklist de postagem.

## Por que isso foi feito

Renderizar MP4 no RunSite causava timeout, 404 da plataforma e rollback. Vídeo é pesado para 0.1 CPU/256MB.

## Novo fluxo

1. Criar produto no site.
2. Abrir o produto.
3. Clicar em **Kit vídeo CapCut/Canva**.
4. Baixar o ZIP.
5. Montar o MP4 no CapCut ou Canva.
6. Postar em TikTok, Reels, Shorts e Status.
