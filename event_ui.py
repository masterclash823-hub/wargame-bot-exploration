"""Discord UI for persisted event adventures; /event play restores expired views."""
import discord
import logging
import i18n

import event_adventure as adventure


def render_event(state):
    lang = state["lang"]
    title = adventure.tr(lang, "Wydarzenie", "Event")
    title += f" #{state['event_id']} — {state['nation'][:150]}"
    embed = discord.Embed(title=title, description=state["text"][:1800], color=discord.Color.purple())
    history = state["history"]
    if history:
        lines = [f"{i}. {h['action'][:180]} (×{adventure.SCALES[h['choice']]:g})"
                 for i, h in enumerate(history, 1)]
        embed.add_field(name=adventure.tr(lang, "Podjęte decyzje", "Decisions"), value="\n".join(lines), inline=False)
        if history[-1].get("fallback"):
            embed.add_field(name="AI", value=adventure.tr(lang,
                "AI niedostępne: własną odpowiedź rozliczono jako zrównoważoną (×1).",
                "AI unavailable: your custom response was treated as balanced (×1)."), inline=False)
    if state["resolved"]:
        embed.add_field(name=adventure.tr(lang, "Zastosowane efekty", "Applied effects"),
                        value=adventure.effects_text(state["applied"], lang)[:1000], inline=False)
        note = state["applied"].get("special_note")
        if note:
            embed.add_field(name=adventure.tr(lang, "Uwaga GM (fabularna)", "GM note (narrative)"), value=note, inline=False)
        embed.set_footer(text=adventure.tr(lang, "Koniec • 3/3 decyzji • efekty naliczone raz", "Finished • 3/3 decisions • effects applied once"))
        return embed
    for i, label in enumerate(state["choices"]):
        total = adventure.prospective_effects(state, [h["choice"] for h in history] + [i])
        embed.add_field(name=f"{i + 1}. {label}"[:256], value=
                        f"×{adventure.SCALES[i]:g} — " + adventure.effects_text(total, lang)[:800], inline=False)
    embed.add_field(name=adventure.tr(lang, "Zasady", "Rules"), value=adventure.tr(lang,
        "Efekty przy opcjach to suma odłożona do finału, nie natychmiastowa wypłata. Każda decyzja wnosi ⅓ skutków. "
        "Skala obejmuje zyski i straty. Własna odpowiedź zostaje przypisana przez AI do jednej z tych trzech strategii; nie ustala dowolnych nagród.",
        "Option effects are the accumulated pending total, not an immediate payout. Each decision contributes ⅓ of effects. "
        "Scaling covers gains and losses. AI maps a custom response to one of these three strategies; it cannot invent rewards."), inline=False)
    embed.set_footer(text=adventure.tr(lang, "Decyzja", "Decision")
                     + f" {len(history) + 1}/3 • /event play {state['event_id']} • "
                     + adventure.tr(lang, "wznów także po restarcie", "resume even after restart"))
    return embed


@i18n.localized
async def respond(interaction, state, choice=None, answer=None):
    await interaction.response.defer(ephemeral=True)
    try:
        updated = await adventure.decide(state["event_id"], state["version"], interaction.user.id, choice, answer)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except Exception:
        logging.exception("Failed to advance event %s", state["event_id"])
        await interaction.followup.send(adventure.tr(state["lang"],
            "Błąd zapisu. Sprawdź aktualny stan przez /event play przed ponownym wyborem.",
            "Save failed. Check the current state with /event play before choosing again."), ephemeral=True)
        return
    await interaction.followup.send(embed=render_event(updated),
                                    view=EventView(updated), ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())


class CustomAnswer(discord.ui.Modal):
    def __init__(self, state):
        super().__init__(title=adventure.tr(state["lang"], "Własne działanie", "Your own action"))
        self.state = state
        self.answer = discord.ui.TextInput(label=adventure.tr(state["lang"], "Co robisz? (liczy się jako decyzja)", "What do you do? (counts as a decision)"),
                                          style=discord.TextStyle.paragraph, min_length=1, max_length=1000)
        self.add_item(self.answer)

    async def on_submit(self, interaction):
        await respond(interaction, self.state, answer=self.answer.value)


class EventView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=600)
        self.state = state
        if state["resolved"]:
            return
        for index in range(3):
            button = discord.ui.Button(label=str(index + 1), style=discord.ButtonStyle.primary)
            button.callback = self._choose(index)
            self.add_item(button)
        other = discord.ui.Button(label=adventure.tr(state["lang"], "Własna odpowiedź", "Custom response"))
        other.callback = self._custom
        self.add_item(other)

    async def interaction_check(self, interaction):
        if str(interaction.user.id) == self.state["owner_id"]:
            return True
        await interaction.response.send_message(adventure.tr(self.state["lang"],
            "Decyzję podejmuje właściciel tego narodu.", "Only this nation's owner can decide."), ephemeral=True)
        return False

    def _choose(self, index):
        async def callback(interaction):
            await respond(interaction, self.state, choice=index)
        return callback

    async def _custom(self, interaction):
        await interaction.response.send_modal(CustomAnswer(self.state))
