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


---

## Etapa 2 — Laboratório de Backtesting (v2)

Tela: /trading-pro/backtest; menu lateral e painel comercial incluem um atalho.

### Escopo entregue

- Janela histórica selecionável de **14, 30 ou 60 dias**, em candles de 1 hora da Binance Spot API pública. Paginação com limite de 1000 por requisição, sem candles abertos ou lacunas aceitas.
- 160 candles anteriores para aquecimento de indicadores. Para simular um candle, os cinco robôs consultam apenas candles já fechados até o anterior. Ordem fictícia executada na **abertura do candle seguinte**.
- Janela cronológica **70% inicial / 30% final**, com capital fictício independente em cada janela para cada estratégia. **Não há treinamento, otimização automática ou walk-forward**: a separação é descritiva e não constitui validação estatística de generalização.
- Benchmark de comprar e manter o mesmo ativo dentro de cada janela, também com taxas e slippage estimados.
- Taxa estimada de **0,10% por lado** e slippage fixo de **0,05% por lado**; retornos líquidos, drawdown, operações fechadas, taxa de acertos, fator de lucro e Sharpe horário anualizado com taxa livre de risco 0%.
- Entradas spot sem alavancagem, com a mesma função de dimensionamento do modo simulado atual, stop de 2–6%, take profit 2:1, circuit breaker diário de 2% e drawdown limite de 8%.
- Modelo OHLC conservador: se stop e alvo tocam no mesmo candle, assume stop primeiro. Gap abaixo do stop assume abertura pior. Liquidação hipotética no fim da janela. Risco é monitorado por candle, **não** tick a tick.
- Os resultados são armazenados na tabela separada tp_backtest_reports por usuário e quantidade de dias; atualização só é disparada por clique autenticado, com token CSRF. Relatórios recuperados são identificados como salvos, nunca confundidos com resultados atualizados em tempo real.
- Hash SHA-256 por par para identificar a série histórica retornada. **Histórico bruto não é arquivado**; o hash não substitui auditoria nem reprodutibilidade integral.
- Testes automatizados offline com fixture, paginação, cenário de look-ahead, stop e benchmark, API sem token, isolamento de dados e verificação de renderização Flask.

### Limitações técnicas e validação necessária

- Não houve, no código da CI, consulta de rede real à Binance; os testes usam preços **sintéticos identificados como fixtures**. Os resultados do painel, quando disponíveis, vêm exclusivamente de consulta à API pública e não há fallback silencioso para dados sintéticos.
- Testes históricos, ainda que positivos, não provam vantagem econômica no futuro. Os resultados dependem do período observado e da seleção das estratégias. Não há simulação de profundidade do livro, variação de spread, dinâmica de slippage, volume, taxas diferenciadas, latência ou indisponibilidade da corretora.
- O Sharpe anualizado de janelas de poucos dias pode ser estatisticamente instável; o profit factor fica indefinido se não houver perdas.
- As ordens históricas não são ordens reais e não há depósitos ou chaves de corretora.
- 60 dias podem requerer múltiplas chamadas externas e superar o timeout da hospedagem atual. Antes de adicionar execução recorrente, migrar a coleta para worker agendado, com cache persistente e observabilidade.
- Antes de considerar capital real: seleção de dados resistente a viés, estratégia pré-registrada, validação walk-forward real, período forward paper com métricas e relatórios, segurança de autenticação e avaliação jurídica/regulatória.

### Executar testes

python -m unittest discover -s tests -p "test_trading*.py" -v

O fluxo GitHub Actions também valida importação Flask, página /trading-pro/backtest e APIs autenticadas.
