# Atualização v12 — Robô Comercial

Esta atualização adiciona a aba **Robô comercial** ao sistema.

## O que o robô faz

- Pega seus produtos cadastrados.
- Monta campanhas automáticas para 7 a 90 dias.
- Cria fila de posts para Instagram Reels, TikTok, YouTube Shorts, Facebook Page e WhatsApp.
- Gera textos adaptados para nichos escolares e outros nichos vendáveis.
- Controla quantidade de posts por dia para evitar spam.
- Mostra diagnóstico dos produtos que ainda estão sem checkout.
- Publica posts vencidos no Facebook Page quando a API oficial estiver configurada.
- Possui endpoint de cron para automação agendada.

## Variáveis opcionais

```env
ROBOT_SECRET=uma_chave_grande_para_cron
PUBLIC_BASE_URL=https://seu-site.runsite.app
FACEBOOK_PAGE_ID=id_da_pagina
FACEBOOK_PAGE_ACCESS_TOKEN=token_da_pagina
INSTAGRAM_ACCOUNT_ID=id_da_conta_profissional
TIKTOK_ACCESS_TOKEN=token_oauth_tiktok
YOUTUBE_CLIENT_SECRET_JSON=client_secret.json
```

## Endpoint de cron

```text
/api/robo-comercial/cron?secret=SEU_ROBOT_SECRET
```

Para criar fila automaticamente pelo cron:

```text
/api/robo-comercial/cron?secret=SEU_ROBOT_SECRET&create_queue=1&days=30
```

## Observação importante

O robô foi feito para automação segura e realista. Ele não entra em contas por navegador escondido e não faz spam em grupos. Para publicar automaticamente em redes sociais, use APIs oficiais e permissões corretas.
