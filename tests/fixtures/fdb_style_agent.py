"""Test fixture: an agent template in the style of FDB-v3's templates.

Parsed by keel.livekit.template (never imported). Tool names and behaviour
are generic test data.
"""

from livekit.agents import Agent, llm

ai_callable_decorator = llm.function_tool


class AssistantFnc:
    @ai_callable_decorator(description="Search for trains to a city on a date.")
    async def search_trains(self, city: str, date: str):
        """
        Args:
            city: Destination city, e.g. 'Pune'
            date: Travel date, e.g. '2026-10-01'
        """

    @ai_callable_decorator(description="Reserve a seat on a train.")
    async def reserve_seat(self, train_id: str, seats: int = 1):
        """
        Args:
            train_id: Train identifier
            seats: Number of seats
        """

    @ai_callable_decorator(description="Convert an amount between currencies.")
    async def convert(self, amount: float, to_currency: str, note: str = None):
        """
        Args:
            amount: Amount to convert
            to_currency: 3-letter code
            note: Optional note
        """


class FixtureAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=("Be brief. " "Use the tools."))
