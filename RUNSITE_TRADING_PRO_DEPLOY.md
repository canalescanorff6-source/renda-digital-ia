# Trading PRO — publicação controlada no RunSite

Este procedimento é para o Web Service existente, não para criar um segundo projeto. O projeto continua **PAPER ONLY**, sem ordens reais.

## Diagnóstico

- Trading PRO v5: já integrado ao GitHub main e aprovado nos testes automatizados.
- Domínio histórico nos documentos do projeto: https://renda-digital-ia.runsite.app
- A verificação externa recente de /trading-pro/monitor e /trading-pro/ping retornou 404. Verifique a URL atual no painel; sem isso, não afirme que o aplicativo está atualizado.
- start.sh usa /tmp/renda_digital_ia como runtime padrão. O RunSite descarta armazenamento efêmero durante troca de contêiner; os registros de simulação precisam de disco persistente.

## 1. Verificar o serviço existente

Abra https://dashboard.runsite.app/login diretamente e faça login sem compartilhar credenciais no chat. No projeto original, encontre o Web Service e sua URL pública.

Em Settings → Build & Deploy, confirme:
- Repositório: canalescanorff6-source/renda-digital-ia
- Branch selecionada: main
- Auto-deploy ligado, se deseja receber atualizações futuras automaticamente
- Logs do último deploy, saúde do contêiner e histórico de builds. Se o build/healthcheck falhar, o RunSite pode manter a versão anterior
- Comando de inicialização via Dockerfile existente e start.sh, ouvindo em 0.0.0.0:$PORT

## 2. Preservar dados antes da migração para disco persistente

**Não reinicie nem troque DATABASE_PATH antes de salvar o banco existente**, se houver. Faça backup e verifique os dados; se necessário, recupere pelo shell de contêiner antes de qualquer substituição.

Localize o caminho efetivo em uso no contêiner. Com o caminho confirmado, use
`python scripts/backup_trading_database.py ORIGEM DESTINO`, informando um nome
novo em uma área privada. A cópia inclui os dados já confirmados do WAL e
passa pelo `integrity_check`. O comando abre a origem somente para leitura.
Guarde uma cópia fora do contêiner antes de substituí-lo e teste a restauração
num caminho separado. Se o serviço ou o arquivo não estiver acessível,
investigue a conta ou a recuperação do armazenamento antes de criar outro serviço.

Ative um disco persistente no Web Service, por exemplo com montagem /data. Somente após backup e migração do banco, use os seguintes valores de ambiente:

| Nome no RunSite | Valor recomendado |
| --- | --- |
| Ponto de montagem do volume | /data, configurado no painel de disco |
| APP_RUNTIME_DIR | /data/renda-digital-ia |
| STORAGE_DIR | /data/renda-digital-ia/storage |
| EXPORT_DIR | /data/renda-digital-ia/storage/exports |
| DATABASE_PATH | /data/renda-digital-ia/storage/renda_digital_ia.db |
| WEB_CONCURRENCY | 1 |
| TRADING_PRO_SCHEDULER_ENABLED | 0 até validar execução |

Não confunda mudar DATABASE_PATH com migrar um banco existente: se a base antiga tiver registros, copie/restaure uma cópia íntegra primeiro. SQLite compartilhado por múltiplos contêineres requer outra arquitetura; mantenha uma instância, sem autoscaling horizontal, enquanto o banco for local.

## 3. Credenciais seguras

Use uma SECRET_KEY forte e privada e uma senha administrativa exclusiva no RunSite. O aplicativo legado pode manter a senha anterior no banco mesmo depois de mudar ADMIN_PASSWORD, por usar INSERT OR IGNORE na criação inicial da conta. Confira a senha da conta existente. Nunca coloque chaves em issues, commits, prints ou neste chat.

## 4. Publicação controlada pelo GitHub Actions

Workflow: .github/workflows/runsite-trading-pro-release.yml

Ele **não publica automaticamente**: exige acionamento manual, disco e segurança confirmados, e API key do RunSite armazenada como GitHub Actions secret.

No RunSite, crie uma API key para CI/CD com acesso restrito ao projeto e capacidade de deploy. No repositório GitHub, abra Settings → Secrets and variables → Actions → New repository secret e defina RUNSITE_API_TOKEN. Não compartilhe seu valor.

No GitHub Actions, selecione o workflow Publicar Trading PRO no RunSite — controlado, use a branch main, informe:
- service_name: nome exato do Web Service. O padrão renda-digital-ia é uma sugestão baseada no domínio antigo, não um nome confirmado
- public_base_url: URL HTTPS real do serviço, sem caminho
- disk_and_security_confirmed: true **somente depois de confirmar backup, volume persistente e senha segura**

O workflow confere a URL canônica, o repositório e a branch main antes de solicitar o deploy pelo CLI oficial. Ele aguarda que o deploy solicitado esteja ativo no commit do workflow e então consulta /trading-pro/ping. Um deploy anterior da mesma versão, uma página 404, um redirecionamento ou uma execução diferente de PAPER_ONLY não passam na verificação.

Se o RunSite já possuir auto-deploy configurado na main, confira antes o histórico de Deploys e a URL publicada. Não é necessário forçar uma nova publicação sem diagnosticar o erro.

## 5. Confirmação pós-deploy

Acesse https://DOMINIO-ATUAL/trading-pro/ping; o JSON deve informar os seguintes valores:

- ok = true
- version = 5.0.0
- execution_mode = PAPER_ONLY

Em /trading-pro/monitor autenticado, confira o pré-check e crie um marco de persistência. Compare o mesmo marco **depois de reiniciar ou republicar**. Apenas o primeiro cadastro não comprova durabilidade.

Somente então teste a API pública da Binance a partir do servidor. Ative ciclos automáticos fictícios apenas se todo o caminho estiver validado. Nunca habilite ordens reais neste procedimento.

## Referências oficiais

- https://docs.runsite.app/services/web-services/deploying/
- https://docs.runsite.app/services/web-services/scaling/
- https://docs.runsite.app/platform/troubleshooting/
- https://runsite.app/tools/cli
