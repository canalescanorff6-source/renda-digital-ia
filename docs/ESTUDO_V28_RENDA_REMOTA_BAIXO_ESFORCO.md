# Proposta V28 — renda remota de baixo esforço com análise financeira verificável

**Data-base: 14 de novembro de 2026.** Proposta de arquitetura e negócio; **não equivale a funcionalidade implantada**, recomendação individual de investimento nem promessa de retorno.

## Objetivos
1. Separar ganhos realizados, valores apenas aprovados, saldos pendentes, lucros líquidos e hipóteses.
2. Identificar fontes de renda que operam com o dispositivo pessoal desligado (juros de investimentos, lojas com checkout/entrega hospedados, assinaturas), sem alegar que são livres de manutenção.
3. Testar oportunidades com investimento incremental, limite de perda e prova documental do resultado.
4. Excluir cliques/impressões automatizados, bots de anúncios, fraude, mineração não autorizada e promessas irreais.

## Evidências de mercado e cenários

### Renda fixa de baixo esforço

- Copom 04/11/2026: Selic de **13,75% a.a.** (Banco Central); **não** assumir que permanecerá igual por um ano.
- Hipótese para comparar cenários: aplicação rendendo taxa **constante de 13,75% a.a. durante 12 meses**, sem taxas, isenções ou rendimentos extraordinários. IR de 17,5% **sobre juros** após 361–720 dias, conforme Tesouro Direto. A rentabilidade anual líquida hipotética é **11,34375%**.
- Exemplos: R$ 1.000 -> R$ 113,44/ano; R$ 5.000 -> R$ 567,19/ano; R$ 10.000 -> R$ 1.134,38/ano. Dividir por 12 é **média contábil**, não um depósito mensal garantido.
- Aplicações específicas divergem do valor hipotético por preço, taxa, CDI efetivo, Selic futura, IR, IOF e custódia. Tesouro Selic tem isenção de custódia da B3 até R$ 10 mil e cobrança nos valores excedentes.
- CDBs elegíveis têm garantia FGC até R$ 250.000 por CPF/conglomerado e R$ 1.000.000 em 4 anos. A garantia tem condições e não produz liquidez instantânea.
- Considerar inflação e disponibilidade de liquidez; os números acima são **nominais**.

### Produtos digitais automatizados

- Plataforma Kiwify informa taxa **8,99% + R$ 2,49 por venda**, sem mensalidade, com checkout e entrega hospedados (conferir plano e taxas da conta antes de contratar).
- Produto a R$ 39,00 -> custo da plataforma R$ 6,00 aproximadamente -> R$ 33,00 disponíveis **antes de impostos, anúncios, reembolsos e outros custos**.
- Dez vendas reais: aproximadamente R$ 330,04 antes de custos adicionais; **zero visitas/clientes = zero vendas**.
- Não usar taxas de conversão de influenciadores como previsão de resultado. Simulações de 0,2%, 1% e 2% servem para entender incerteza, não para afirmar demanda.
- Checkout, emissão/tributação aplicável, política de privacidade, entrega, autorização de uso dos materiais e atendimento precisam funcionar.

### Mineração em nuvem

- Hashrate Index em 09/11/2026: hashprice **US$ 31,89 por PH/s por dia**, RECEITA BRUTA estimada, antes de energia, aluguel, taxas, obsolescência e impostos.
- Um contrato hipotético de **200 TH/s = 0,2 PH/s** geraria apenas **US$ 6,38 por dia brutos** nesse cenário, antes de todos os custos. É necessário conhecer o custo *total* do contrato em dólares/dia, tempo contratual, variação de dificuldade, prova de hashrate e termos de saída.
- Como comparação de energia, uma máquina de 3,5 kW ligada por 24h consome 84 kWh/dia; ao custo hipotético de US$ 0,08/kWh, gastaria US$ 6,72/dia só com energia -> cenário já negativo antes de hardware e operação. **Isso não é cotação de provedor cloud**.
- Nenhum projeto de cloud mining foi aprovado como investimento com rentabilidade demonstrada nesta proposta. Não recomendar depósitos com promessa de rendimento fixo.

### Robôs de anúncios

- Proibir criação de impressões/clicks simulados. Google AdSense define anúncios vistos/clicados artificialmente como tráfego inválido; risco de banimento e prejuízo de anunciantes.
- Um agregador de programas com *links oficiais e tarefas executadas por humanos* é permitido conforme termos de cada programa, mas os ganhos são pequenos e descontínuos.

## Arquitetura a partir do repositório atual

O projeto utiliza **Flask, SQLite, Jinja**, possui `app.py`, `templates/`, tabela `products`, `sales`, `leads`, páginas públicas e pacotes de divulgação. A proposta anterior da V27 mantém a central `/monetizacao-real` numa branch e PR separado em revisão.

**Módulos da V28:**

1. `economia/returns.py`: funções puras de juros anuais, IR regressivo, IOF se aplicável, inflação, custódia, rendimento líquido e diferença entre valores *estimados* e *realizados*. Suportar cenários de queda de Selic.
2. `economia/digital_products.py`: planilha de margem por preço, gateway, aquisição, reembolso, impostos informados, SLA e lucro por venda. Conversão com intervalo de incerteza, sempre começando em zero vendas.
3. `economia/mining.py`: hashprice * hashrate - aluguel - hosting/energia - pool - câmbio - tributos. Testar break-even, queda do BTC, aumento da dificuldade, expiração do contrato e risco de contraparte. Exibir "não recomendado" sem documentação de custos.
4. `economia/ledger.py`: lançamentos e evidências, `source_type`, `user_id`, `gross`, `fees`, `taxes`, `refunds`, `net`, `currency`, `status` (estimado/pendente/aprovado/recebido), data, documento, integração e hash idempotente. Totais de caixa contabilizam **apenas recebido**; sem taxas automáticas inventadas.
5. `economia/monitor.py`: pesquisa agendada de tarifas/juros e variação de hashprice em fontes públicas, com data de atualização, cache e alerta de preço/risco; sem movimentar dinheiro.
6. `templates/centro_renda_inteligente.html`: dashboard com três trilhas: `Renda financeira`, `Negócios automatizáveis`, `Experimentos de alto risco`; gerador de relatórios e limites de exposição.
7. `integrations/checkout.py`: conectar apenas contas que o usuário autorizar; sincronização de vendas aprovadas/estornadas por webhook assinado, log auditável e deduplicação por transaction_id. Nunca transformar "clicou" em "ganhou".
8. `tests/test_economia_*.py`: testes de arredondamento Decimal, datas de imposto, cenário de prejuízo, FGC, multiusuário, autorização, webhook replay e valores em múltiplas moedas.

## Regras de segurança/qualidade (critérios de aceite)

- Antes de qualquer merge, corrigir defaults inseguros de `SECRET_KEY`, `ADMIN_PASSWORD` e `WEBHOOK_SECRET` de `app.py`, com configuração segura de produção e migração controlada.
- Verificar persistência/backup da base SQLite; migrar para Postgres se houver usuários simultâneos e atualizações frequentes.
- Não armazenar senhas bancárias, seed phrases, credenciais de exchanges em texto puro nem cartões. Dados financeiros só via provedores oficiais e consentimento.
- Não integrar corretagem ou depósitos/saques automáticos nesta fase.
- Registrar fonte e horário de cada taxa de mercado; nunca mostrar dados de novembro de 2026 como cotação atemporal.
- Testar primeiro em branch, depois CI, depois homologação; só publicar após autorizar e validar condições da hospedagem.

## Sequência para validação financeira de 30 dias

**Semana 1:** escolher o teto de capital e a parcela líquida que não precisa de reserva de emergência; preparar simulações e validar oferta digital.

**Semana 2:** colocar UMA oferta real com checkout, material original e entrega automática; comprar/testar o fluxo em ambiente seguro, documentar custos.

**Semana 3:** divulgar conteúdo original permitido, sem bots nem compra de views; aferir tráfego real e taxa de resposta, com transparência publicitária.

**Semana 4:** fechar livro-caixa (receita recebida, taxas, impostos, reembolsos, custos, horas trabalhadas). Encerrar experiências com prejuízo ou sem demanda, sem aumentar investimento para 'recuperar' perdas.

## Fontes

- BCB (novembro de 2026): https://www.bcb.gov.br/publicacoes/atascopom/04112026
- Regras Tesouro: https://www.tesourodireto.com.br/sobre-o-tesouro/regras-e-regulamento
- FGC: https://www.fgc.org.br/pt-br/sobre-garantia-fgc
- Guia de títulos bancários CVM: https://www.gov.br/investidor/pt-br/investir/tipos-de-investimentos/titulos-bancarios
- Kiwify taxas: https://ajuda.kiwify.com.br/pt-br/article/perguntas-frequentes-165uytc/
- Hashrate Index 09/11/2026: https://hashrateindex.com/blog/hashrate-index-roundup-november-9-2026/
- CVM fraudes: https://www.gov.br/cvm/pt-br/assuntos/protecao/mercado-forex
- Google AdSense tráfego inválido: https://support.google.com/adsense/answer/16737

**Estado:** especificação/pesquisa. Não executa trades, mineradores, anúncios nem alterações em produção.
