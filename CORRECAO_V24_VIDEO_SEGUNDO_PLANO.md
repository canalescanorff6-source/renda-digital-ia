# Correção v24 — Vídeo seguro para RunSite

Esta versão muda a geração do vídeo para não travar o RunSite.

## O que foi alterado

- `/produtos/<id>/gerar-video` agora abre uma tela rápida, sem tentar renderizar o MP4 inteiro na mesma requisição.
- O vídeo é gerado em segundo plano por uma thread leve.
- A página consulta `/produtos/<id>/video-status` automaticamente até o vídeo ficar pronto.
- O MP4 agora é mais leve: 544x960, FPS reduzido e poucas cenas.
- Botões seguros:
  - `Gerar vídeo`
  - `Regenerar vídeo`
  - `Assistir vídeo`
  - `Baixar MP4`
- O cache do PWA foi atualizado para `renda-digital-ia-pro-v24`.

## Por que isso resolve

Antes o RunSite podia matar a requisição porque o vídeo era pesado e era gerado enquanto a página estava aberta. Agora a página responde rápido e o MP4 fica sendo preparado por trás.

## Como testar

1. Entre no sistema.
2. Vá em `Meus produtos`.
3. Abra um produto.
4. Clique em `Vídeo MP4 temático`.
5. Aguarde a mensagem mudar para `Vídeo pronto`.
6. Clique em `Baixar MP4`.

## Observação

A geração de vídeo no RunSite ainda deve ser leve, porque o plano mostrado tinha pouca CPU/RAM. Para vídeos mais longos e pesados, use um servidor com mais recurso.
