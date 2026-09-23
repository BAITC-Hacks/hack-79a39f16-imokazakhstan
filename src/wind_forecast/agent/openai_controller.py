"""Person 3: bounded OpenAI function-calling controller integration point."""


class OpenAIController:
    """Replace the TODO with a Responses API tool loop over approved local functions.

    Keep the offline workflow as the default. Register only explicit functions such as
    fetch_weather, audit_inputs, predict_power, inspect_forecast, and save_forecast.
    Enforce issue-time/provenance checks in Python even if the model requests an unsafe action.
    Cap tool rounds and retries; never expose shell execution or credentials as tools.
    """

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the live controller")
        if not model:
            raise ValueError("Set OPENAI_MODEL to a model enabled for the team's API key")
        self.api_key = api_key
        self.model = model

    def run(self, request: object) -> object:
        raise NotImplementedError(
            "Person 3: implement a bounded Responses API function-calling loop; "
            "the fixture workflow works without this integration."
        )
