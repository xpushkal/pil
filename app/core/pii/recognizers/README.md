# Custom PII recognizers

This directory holds Presidio `EntityRecognizer` implementations that ship with PIL.
Any `.py` file dropped in here that exports `RECOGNIZERS: list[EntityRecognizer]` is
auto-registered at startup by `app/core/pii/scrubber.py`.

## Enabled Presidio built-ins (Sprint 1)

`PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `CREDIT_CARD`, `IBAN_CODE`, `IP_ADDRESS`,
`URL`, `LOCATION`, `DATE_TIME`.

## Explicitly disabled

Per founder direction, the following government / regulated-document entities are
**not** loaded — PIL must never see, log, or persist them:

- `US_SSN`
- `US_DRIVER_LICENSE`
- `US_PASSPORT`
- `UK_NHS`
- `IN_AADHAAR`
- `MEDICAL_LICENSE`

If a downstream customer needs gov-ID handling, that work happens behind a separate
opt-in flag and a security review — do not flip these on without one.

## Custom dev-secret recognizers (the differentiator)

These ship in Sprint 1 Phase 2:

| File | Entity |
| --- | --- |
| `openai_key.py` | `OPENAI_API_KEY` |
| `anthropic_key.py` | `ANTHROPIC_API_KEY` |
| `aws_keys.py` | `AWS_ACCESS_KEY` + `AWS_SECRET_KEY` (contextual to access key) |
| `github_token.py` | `GITHUB_TOKEN` |
| `private_key.py` | `PRIVATE_KEY_BLOCK` |
| `db_connection.py` | `DATABASE_CONNECTION` |
| `jwt.py` | `JWT_TOKEN` |

## Adding a recognizer

```python
# app/core/pii/recognizers/my_thing.py
from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

MY_THING = PatternRecognizer(
    supported_entity="MY_THING",
    patterns=[Pattern(name="my-pattern", regex=r"...", score=0.9)],
)

RECOGNIZERS: list[EntityRecognizer] = [MY_THING]
```

The plugin loader picks it up on next start. A test under
`tests/unit/pii/test_recognizer_plugin_loader.py` exercises this with a fixture
recognizer.
