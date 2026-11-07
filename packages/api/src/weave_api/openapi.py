import json

from .main import create_app


def main() -> None:
    print(json.dumps(create_app(service=None).openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
