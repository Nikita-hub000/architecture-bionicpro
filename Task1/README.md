## Что реализовано
### Инфраструктура (Docker Compose)
- Поднят стек контейнеров: **PostgreSQL для Keycloak**, **Keycloak 21.1**, **OpenLDAP**, **frontend (React)**, **bionicpro-auth (BFF)**, . **reports-api**
- Все сервисы связаны общей сетью, BFF ходит в reports-api по внутреннему адресу контейнера.

### Безопасная авторизация (PKCE + BFF)
- Реализован **Authorization Code Flow + PKCE** через сервис . **bionicpro-auth**
- Токены **не выдаются фронтенду**: браузер получает только **HTTP-only cookie** с session id.
- На BFF реализовано:
    - хранение `access_token`/`refresh_token` на сервере,
    - автоматическое обновление `access_token` через `refresh_token`,
    - ротация session id (защита от session fixation).

### Reports API
- Добавлен отдельный сервис с эндпоинтом `GET /reports`. **reports-api**
- защищён Bearer-токеном и валидирует JWT через **JWKS Keycloak**. `reports-api`
- BFF проксирует запрос `GET /api/reports` в и возвращает данные пользователю. `reports-api`

### Frontend (React)
- UI ходит **только в BFF** (`/login`, `/api/reports`) и всегда отправляет cookie (`credentials: include`).
- Данные отчёта, полученные от API, **выводятся на странице** (не просто “200 OK”).

### LDAP (User Federation + маппинг ролей)
- Добавлен **LDAP User Federation** в Keycloak (через `components` в realm-export).
- LDAP-структура:
    - пользователи: `ou=People,dc=example,dc=com`
    - группы/роли: `ou=Groups,dc=example,dc=com`

- Настроен **Group LDAP Mapper**: пользователи получают Keycloak-группы по membership LDAP-групп.
- В Keycloak заведены группы `user`, с привязкой к realm roles. `prothetic_user`

### MFA (OTP)
- В включена политика OTP и сделано MFA обязательным:
    - добавлен required action (по умолчанию), `CONFIGURE_TOTP`
    - настроен кастомный browser flow с обязательным . `browser-mfa``auth-otp-form`

`keycloak/realm-export.json`

### Яндекс ID (OAuth2) — состояние
- Добавлен блок `identityProviders` + `identityProviderMappers` в realm-export как задел под Яндекс ID.
- Сейчас интеграция **не завершена**: текущий `providerId`/настройки требуют корректной поддержки Keycloak (в 21.1 generic OAuth2 IdP “oauth2” не импортируется), поэтому нужна финальная правка схемы identity brokering под доступный провайдер/версию Keycloak.