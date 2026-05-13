from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    secret_key: str
    cookie_name: str = "session"
    cookie_max_age: int = 60 * 60 * 24 * 365
    wikipedia_base_url: str = "https://en.wikipedia.org/api/rest_v1"
    wikipedia_action_url: str = "https://en.wikipedia.org/w/api.php"
    feed_size: int = 8
    region: str = ""   # ISO 3166-1 alpha-2 country code for Top Daily (e.g. US, GB, IN). Empty = global.
    debug: bool = False

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
