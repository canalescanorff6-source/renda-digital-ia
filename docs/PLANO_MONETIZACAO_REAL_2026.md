# Renda Digital IA — plano de monetização validável (novembro de 2026)

> Este projeto não promete renda passiva nem vendas automáticas. A plataforma organiza hipóteses, materiais, campanhas e resultados. Ganhos só existem após transações válidas; números informados manualmente não são prova independente de receita.

## Prioridade de negócio

**Piloto A — produto próprio:** um kit original e específico (ex.: planner para professores, checklist escolar, material autoral de papelaria). Testar demanda com amostra útil, página pública, checkout real e vídeos de demonstração curtos com voz ou análise própria. Escolher **um** produto antes de ampliar catálogo.

**Piloto B — afiliados:** após criar conta e receber aprovação quando aplicável, selecionar apenas produtos que possam ser demonstrados de forma honesta. Usar exclusivamente links de afiliado fornecidos pela plataforma e declarar publicidade. Não prometer lucros aos clientes nem fingir uso de produto.

**Não priorizar agora:** tráfego pago, robots de comentários/DM, compra de seguidores, vídeos reutilizados em massa, scraping de vídeos, conteúdo que use imagem/marca sem autorização e integrações que publiquem sem revisão.

## Fluxo semanal de menor esforço

1. Uma sessão para revisar produto, oferta e checkout (antes de qualquer divulgação).
2. Uma sessão para gerar um kit de conteúdo usando os recursos existentes do aplicativo.
3. Publicar manualmente 2–3 peças **originais** em canais permitidos, com link certo e aviso de publicidade quando houver comissão.
4. Checar resultados e feedback uma vez por semana, registrar cliques/vendas e **dinheiro recebido** em `/monetizacao-real`.
5. Repetir a oferta que mostrar sinais reais de compra; corrigir ou encerrar hipóteses sem tração. Não há percentual mínimo de conversão garantido.

## O que a nova central faz

- Apresenta os programas oficiais de Mercado Livre, Hotmart, TikTok Shop e Amazon.
- Simula cenários de receitas sem gravá-los como vendas.
- Guarda testes e números **informados manualmente**, segregados por usuário.
- Calcula dinheiro recebido menos gastos registrados, sem inventar pagamentos.
- Orienta um experimento de sete dias e redireciona para páginas já existentes do aplicativo.

## O que ela **não** faz

- Não cadastra contas de afiliado, não gera links rastreáveis em nome de terceiros, não confirma vendas no banco, não recebe PIX e não publica automaticamente.
- Não consulta dados internos de TikTok, Instagram, YouTube ou programas de afiliados sem autorização de conta e API.
- Não converte cenários hipotéticos em ganhos reais.

## Ações obrigatórias antes de publicar em produção

1. Configurar `SECRET_KEY` segura, `ADMIN_EMAIL` próprio e `ADMIN_PASSWORD` forte nos segredos da hospedagem. **O app legado ainda contém valores padrão inseguros**, e a proteção do login depende das variáveis corretas.
2. Verificar se SQLite está hospedado em volume **persistente**, com backup externo. Se a plataforma apagar `storage/` a cada reinício/deploy, os registros serão perdidos.
3. Validar que o frontend carrega, que `/monetizacao-real` exige login, que o registro de teste funciona e que URLs e CSRF são verificados.
4. Confirmar que checkout, webhooks e produtos atuais seguem operando normalmente; não expor dados de compradores.
5. Executar `python -m unittest discover -s tests -p test_monetizacao_real.py -v` em ambiente com Flask, e observar a CI.
6. Publicar apenas após confirmar segurança e testes. Um PR aberto não é uma versão implantada.

## Links de fontes, políticas e treinamentos

- Mercado Livre — https://www.mercadolivre.com.br/l/afiliados-home
- Central de afiliados Mercado Livre — https://www.mercadolivre.com.br/l/checklist
- Vídeo oficial Mercado Livre (14/01/2026) — https://www.youtube.com/watch?v=7fe3dgqIsws
- Hotmart — https://hotmart.com/pt-br/afiliados
- Vídeo oficial Hotmart para afiliados — https://www.youtube.com/watch?v=5ZfTObFtHJg
- TikTok Shop, critérios (revisados em 2026) — https://seller-br.tiktok.com/university/essay?knowledge_id=1396756526679824
- TikTok Shop, inscrição — https://seller-br.tiktok.com/university/essay?knowledge_id=6807244117362448
- Amazon Associados — https://associados.amazon.com.br/
- Amazon, análise de candidatura — https://associados.amazon.com.br/help/node/topic/G8TW5AE9XL2VX9VM
- YouTube, conteúdo original exigido — https://support.google.com/youtube/answer/1311392?hl=pt-BR
- CONAR, guia de publicidade por influenciadores (maio 2026) — https://www.conar.org.br/diretrizes
- Canva, licenças de produtos para venda — https://www.canva.com/pt_br/help/using-canva-to-create-products-for-sale/

Fontes públicas e regras mudam. Verifique os requisitos em cada programa antes de cadastrar ou divulgar.
