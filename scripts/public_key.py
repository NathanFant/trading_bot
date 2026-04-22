import nacl.signing  # type: ignore[import-untyped]
import base64
from pathlib import Path

ENV_FILE = Path(__file__).parent.parent / ".env.local"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def save_env(env: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")


def generate_and_save_keypair() -> tuple[str, str]:
    private_key = nacl.signing.SigningKey.generate()
    public_key = private_key.verify_key

    private_b64: str = base64.b64encode(private_key.encode()).decode()  # type: ignore[arg-type]
    public_b64: str = base64.b64encode(public_key.encode()).decode()  # type: ignore[arg-type]

    env = load_env()
    env["ROBINHOOD_PRIVATE_KEY"] = private_b64
    env["ROBINHOOD_API_KEY"] = ""
    save_env(env)

    return private_b64, public_b64


def main() -> None:
    env = load_env()
    existing_private: str = env.get("ROBINHOOD_PRIVATE_KEY", "")

    if existing_private:
        print("Existing private key found in .env — loading it.")
        private_key = nacl.signing.SigningKey(base64.b64decode(existing_private))
        public_b64: str = base64.b64encode(private_key.verify_key.encode()).decode()  # type: ignore[arg-type]
    else:
        print("No existing key found — generating new keypair.")
        _, public_b64 = generate_and_save_keypair()
        print("Private key saved to .env as ROBINHOOD_PRIVATE_KEY.\n")

    print("=" * 60)
    print("PUBLIC KEY (register this with Robinhood):")
    print(public_b64)
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Go to https://robinhood.com/crypto/developer")
    print("  2. Create a new API key and paste the public key above")
    print("  3. Robinhood will give you an API Key ID")
    print("  4. Paste that API Key ID into .env as ROBINHOOD_API_KEY=<value>")
    print()
    print("Your private key is stored in .env — never share or commit it.")


if __name__ == "__main__":
    main()
