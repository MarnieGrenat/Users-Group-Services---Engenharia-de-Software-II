# Service Layer — User & Group Service

API REST (FastAPI) que expõe os endpoints do [openapi.yaml](../openapi.yaml).
Toda a lógica de negócio vive nas stored procedures (ver [`../db`](../db)); esta
camada apenas autentica/autoriza, valida payloads e chama as funções `sp_*`.

## Estrutura

```
src/
  main.py                # entrypoint (cria o app, bind em loopback)
  app/
    config.py            # settings via variáveis de ambiente (UG_*)
    db.py                # pool de conexões + chamada parametrizada das sp_*
    errors.py            # ServiceError, mapeamento SQLSTATE -> Erro (RFC 7807)
    security.py          # autenticação (X-Service-Id/X-User-Id) e autorização
    schemas.py           # modelos Pydantic (camelCase) espelhando o contrato
    etags.py             # If-Match -> versão; ETag de usuário
    services/            # uma função por stored procedure
      groups.py
      users.py
    api/                 # rotas HTTP
      groups.py
      users.py
```

## Executando

```bash
pip install -r requirements.txt
cp .env.example .env          # ajuste UG_DATABASE_URL
# aplique as migrações de ../db antes (V001, V002)
python main.py                # ou: uvicorn main:app
```

## Segurança — decisões de projeto

- **SQL injection:** as funções `sp_*` são chamadas exclusivamente com
  placeholders (`%s`); nenhum valor é interpolado em string SQL (`app/db.py`).
- **Autenticação fail-closed:** apenas `X-Service-Id` da allowlist é aceito;
  `X-User-Id` deve ser inteiro positivo. Qualquer desvio → `401` (`app/security.py`).
- **Autorização por permissão:** serviços somente-leitura recebem `403
  READ_ONLY_SERVICE` em qualquer escrita (`require_write`).
- **Sem vazamento de internals:** falhas inesperadas viram `500 INTERNAL_ERROR`
  genérico; o detalhe é apenas logado, nunca devolvido (`app/__init__.py`).
- **Validação de entrada:** modelos de requisição rejeitam campos extras
  (`extra="forbid"`) e validam tamanhos; `pageSize` é limitado a 100 — barreiras
  contra payloads abusivos e exaustão de recursos.
- **Limites de recurso:** `statement_timeout` por conexão e pool com tamanho
  máximo protegem o banco.
- **Backend-only:** bind padrão em `127.0.0.1`; confiança nos cabeçalhos
  pressupõe mTLS no service mesh. Não há validação de JWT aqui (feita no edge).
- **Privilégio mínimo no banco:** use um usuário com apenas `EXECUTE` nas
  funções `sp_*` (sem acesso direto às tabelas). Exemplo:

  ```sql
  GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO ug_app;
  ```

> Rate limiting (`429`) e o término TLS são responsabilidade da
> infraestrutura/gateway, fora do escopo deste serviço.
