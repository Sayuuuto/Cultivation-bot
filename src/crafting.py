from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .content import RecipeDef, get_recipe, get_recipes
from .drop_sources import format_missing_materials_message
from .inventory import add_item, get_item_name, has_items, remove_item
from .models import Player
from .pill_recipes import recipe_available_for_realm, resolve_recipe_inputs


@dataclass
class CraftResult:
    success: bool
    recipe_name: str
    crafted: dict[str, int] = field(default_factory=dict)
    byproducts: dict[str, int] = field(default_factory=dict)
    message: str = ""


def _craft_once(
    session: Session,
    player: Player,
    recipe: RecipeDef,
    rng: random.Random,
) -> CraftResult:
    inputs = resolve_recipe_inputs(recipe, player.realm_index)
    if not recipe_available_for_realm(recipe, player.realm_index):
        return CraftResult(
            success=False,
            recipe_name=recipe.name,
            message=f"**{recipe.name}** unlocks at a higher cultivation realm.",
        )
    if not has_items(session, player.id, inputs):
        if recipe.recipe_type == "key":
            action = "key"
        elif recipe.recipe_type == "pill":
            action = "pill"
        else:
            action = "craft"
        return CraftResult(
            success=False,
            recipe_name=recipe.name,
            message=format_missing_materials_message(
                session, player.id, inputs, action=action
            ),
        )

    for item_id, qty in inputs.items():
        remove_item(session, player.id, item_id, qty)

    crafted: dict[str, int] = {}
    byproducts: dict[str, int] = {}

    if rng.random() <= recipe.success_chance:
        add_item(session, player.id, recipe.output_item_id, recipe.output_quantity)
        crafted[recipe.output_item_id] = recipe.output_quantity
        msg = f"You successfully craft **{recipe.name}**."
    else:
        msg = f"Your alchemy falters; **{recipe.name}** does not form."
        if recipe.byproduct_item_id:
            add_item(session, player.id, recipe.byproduct_item_id, 1)
            byproducts[recipe.byproduct_item_id] = 1
            msg += f" Leftover {get_item_name(recipe.byproduct_item_id)} remains."

    return CraftResult(
        success=bool(crafted),
        recipe_name=recipe.name,
        crafted=crafted,
        byproducts=byproducts,
        message=msg,
    )


def craft_recipe(
    session: Session,
    player: Player,
    recipe_id: str,
    amount: int = 1,
    rng: random.Random | None = None,
) -> CraftResult:
    rng = rng or random.Random()
    amount = max(1, min(amount, 10))

    recipe = get_recipe(recipe_id)
    if recipe is None:
        return CraftResult(success=False, recipe_name=recipe_id, message="Unknown recipe.")

    total_crafted: dict[str, int] = {}
    total_byproducts: dict[str, int] = {}
    successes = 0
    messages: list[str] = []

    for _ in range(amount):
        res = _craft_once(session, player, recipe, rng)
        if not res.success and not res.byproducts:
            return res
        if res.success:
            successes += 1
        for item_id, qty in res.crafted.items():
            total_crafted[item_id] = total_crafted.get(item_id, 0) + qty
        for item_id, qty in res.byproducts.items():
            total_byproducts[item_id] = total_byproducts.get(item_id, 0) + qty
        messages.append(res.message)

    if amount == 1:
        summary = messages[0]
    else:
        summary = f"Crafted {successes}/{amount} **{recipe.name}**."
        if total_byproducts:
            ash = total_byproducts.get("pill_ash", 0)
            if ash:
                summary += f" Pill ash: {ash}."

    return CraftResult(
        success=successes > 0,
        recipe_name=recipe.name,
        crafted=total_crafted,
        byproducts=total_byproducts,
        message=summary,
    )


def find_recipe_by_output(output_item_id: str) -> RecipeDef | None:
    for recipe in get_recipes().values():
        if recipe.output_item_id == output_item_id:
            return recipe
    return None


def list_pill_recipes(*, realm_index: int | None = None) -> list[RecipeDef]:
    recipes = [r for r in get_recipes().values() if r.recipe_type == "pill"]
    if realm_index is None:
        return recipes
    return [r for r in recipes if recipe_available_for_realm(r, realm_index)]
