# Renda Digital IA Trading PRO — MVP 1.0

## O que está entregue

Módulo novo e independente em /trading-pro, protegido pelo mesmo login do Renda Digital IA. Preserva a plataforma comercial original.

- 5 robôs de estratégia: Atlas (EMA), Orion (rompimento), Nexus (reversão à média), Pulse (momentum), Sentinel (tendência de menor volatilidade).
- Pares: BTCUSDT, ETHUSDT e SOLUSDT, usando o mercado spot.
- Consulta pública a Binance /api/v3/klines e /api/v3/ticker/price, sem chaves nem permissões de negociação.
- Leitura de candles de 1h fechados (160 solicitados); uma execução de sinal por candle/robô.
- Carteira fictícia por usuário de 10.000 USDT, operações com taxas estimadas de 0,10% e slippage estimado de 0,05% por lado.
- Exposição máxima de 10% por robô e 40% total; orçamento de risco de 0,5% do patrimônio por entrada; stop dinâmico entre 2% e 6% e take profit de 2x a distância do stop.
- Circuit breaker de perda diária de 2% ou drawdown de 8%, com liquidação fictícia e bloqueio de retomada automática.
- Trilhas auditáveis de sinais, operações, custos e motivos em tabelas SQLite isoladas, sem interferir nos produtos e vendas existentes.
- CSRF para requisições de escrita, isolamento pelo usuário autenticado.
- Testes offline de limites, idempotência, proteção de risco, isolamento e CSRF.

## O que NÃO faz

- Não movimenta criptomoedas reais; não há endpoints de compra/venda reais no código.
- Não implementa conexão com chaves de API de corretora.
- Não executa em background nem garante funcionamento 24h. Um clique realiza um ciclo manual. Não confundir com cron agendado.
- Não realiza backtest walk-forward nem validação fora da amostra nesta etapa.
- Não garante lucro, desempenho ou liquidez. Taxas e slippage são hipóteses de simulação; não são comprovantes da corretora.
- A marcação a mercado usa a última cotação coletada no último ciclo. Dashboard entre ciclos pode estar desatualizado.
- Se o provedor bloquear a API pública da Binance, o módulo informa erro e não gera preços falsos.
- Quando um candle ou preço de mercado oscila antes do próximo ciclo, stops não serão executados no momento exato do toque.
- USDT não é uma moeda estatal nem uma garantia de estabilidade.

## Como testar

1. Garanta o ambiente do Flask do repositório (requirements.txt) e banco SQLite persistente.
2. Acesse /trading-pro com usuário autenticado.
3. Clique em "Executar ciclo de análise"; o módulo consulta candles e tickers públicos.
4. Observe sinais, operações, exposição, custos e posições. Novo clique no mesmo candle não duplica operações.
5. Pause/retome manualmente. Bloqueio por risco exige revisão; não há botão de reset que esconda perdas.

Testes: python -m unittest discover -s tests -p "test_trading_pro.py" -v

## Critérios para segunda fase

Antes de estudar uso de capital real, adicionar backtest rigoroso com janelas fora da amostra, execução na barra seguinte, spread/slippage variáveis, taxas, latência, liquidez, métricas (drawdown, Sharpe, expectativa, profit factor), cenários de estresse, dados com timestamps confiáveis e validação forward paper durante período relevante. Avaliar termos e legislação da jurisdição; começar por uso pessoal e sem custódia de terceiros.

Auditar também a autenticação legada: app.py contém valores padrão para usuário, senha e SECRET_KEY, que devem ser trocados em uma implantação pública. Verificar SSL/TLS, backups, proteção anti-CSRF das telas antigas e persistência do SQLite da hospedagem antes de liberar múltiplos usuários.

## Arquitetura

- app.py importa register_trading_pro imediatamente antes do ponto de entrada Python.
- trading_pro.py faz o registro das rotas e inicializa tabelas separadas.
- templates/trading_pro.html é um painel responsivo que compartilha o layout da aplicação.
- Nenhuma dependência Python nova: Flask e requests já estão no requirements.txt.

Esta versão é uma fundação experimental, não um serviço de investimento pronto.
