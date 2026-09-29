"""Discord UI for persisted event adventures; /event play restores expired views."""
import discord
import asyncio
import logging
import i18n
import event_media
from event_text import wrong_language
from nation_access import can_manage

import event_adventure as adventure
from flags import flagged_embed


def illustrate_event(embed, state):
    """The illustration occupies the large image slot; flags use thumbnails.

    Keep the persisted public_image key so existing events can recover their
    illustration in DMs, resumed decisions and the final result as well.
    """
    image = event_media.metadata(state['event_id']) or state.get('public_image')
    if image:
        embed.set_image(url='attachment://'+image['attachment'] if image.get('attachment') else image['url'])
        embed.url = image.get('source') or None
        credit = ' · '.join(filter(None,(image['credit'],image.get('license'),image.get('provider','Wikimedia Commons'))))
        footer = embed.footer.text
        embed.set_footer(text=(f'{footer}\n{credit}' if footer else credit)[:2048])
    return embed


def render_public_event(state):
    """Only the approved opening and sourced illustration are public."""
    embed = discord.Embed(title=f"{state['nation'][:150]} — #{state['event_id']}",
                          description=state['opening'][:4096], color=discord.Color.purple())
    return illustrate_event(embed, state)


def render_event(state):
    lang = state["lang"]
    title = adventure.tr(lang, "Wydarzenie", "Event")
    title += f" #{state['event_id']} — {state['nation'][:150]}"
    text=state['text']
    if not state.get('resolved') and wrong_language(text,lang):
        text=adventure.tr(lang,'Scena wymaga ponownego załadowania w języku państwa. Użyj przycisku poniżej.',
                              'Reload the scene in the nation’s language using the button below.')
    embed = discord.Embed(title=title, description=text[:1800], color=discord.Color.purple())
    flagged_embed(embed, (state.get('flag', ''), state['nation']))
    history = state["history"]
    if history:
        for i, decision in enumerate(history, 1):
            contribution = adventure.effects_text(adventure.prospective_effects(state, [decision]), lang)
            reason = decision.get("reason", "")
            value = f"{decision['action'][:300]}\n↳ {contribution}"
            if reason:
                value += f"\n{reason[:300]}"
            embed.add_field(
                name=f"{adventure.tr(lang, 'Decyzja', 'Decision')} {i}/3",
                value=value[:1024], inline=False,
            )
        if history[-1].get("fallback"):
            embed.add_field(name="AI", value=adventure.tr(lang,
                "AI niedostępne: własną odpowiedź przypisano do strategii zrównoważonej.",
                "AI unavailable: your custom response was treated as balanced."), inline=False)
    if state["resolved"]:
        embed.add_field(name=adventure.tr(lang, "Zastosowane efekty", "Applied effects"),
                        value=adventure.effects_text(state["applied"], lang)[:1000], inline=False)
        note = state["applied"].get("special_note")
        if note:
            embed.add_field(name=adventure.tr(lang, "Uwaga GM (fabularna)", "GM note (narrative)"), value=note, inline=False)
        embed.set_footer(text=adventure.tr(lang, "Koniec • 3/3 decyzji • efekty naliczone raz", "Finished • 3/3 decisions • effects applied once"))
        return illustrate_event(embed, state)
    retry=adventure.needs_scene_retry(state)
    for i, label in enumerate([] if retry else state["choices"]):
        embed.add_field(name=f"{i + 1}. {label}"[:256], value=
                        adventure.tr(lang, "Skutek zależy od sensu decyzji. Limit: ",
                                     "Outcome depends on the decision. Limit: ")
                        + adventure.effect_limits_text(state)[:750], inline=False)
    if retry:
        embed.add_field(name=adventure.tr(lang,"Wybory awaryjne","Fallback choices"), value=adventure.tr(lang,
            "Nie ma jeszcze konkretnych odpowiedzi do tej sceny. Kliknij „Załaduj odpowiedzi ponownie” — nie zużyjesz decyzji ani zasobów. Możesz też opisać własne działanie. Ogólne opcje 1/2/3 są zablokowane.",
            "Specific choices are not ready. Click Reload choices without spending a decision or resources, or describe your own action. Generic options 1/2/3 are disabled."), inline=False)
    embed.add_field(name=adventure.tr(lang, "Zasady", "Rules"), value=adventure.tr(lang,
        "Każda decyzja wnosi ⅓ końcowego skutku. AI ocenia osobno wpływ na złoto, stabilność i zasoby, więc znak może się odwrócić. "
        "GM nadal ustala dozwolone rodzaje oraz maksymalną skalę efektów; AI nie może stworzyć nowych nagród.",
        "Each decision contributes ⅓ of the final outcome. AI assesses gold, stability and resources separately, so a sign may reverse. "
        "The GM still controls allowed effect types and maximum magnitude; AI cannot invent new rewards."), inline=False)
    embed.set_footer(text=adventure.tr(lang, "Decyzja", "Decision")
                     + f" {len(history) + 1}/3 • /event play {state['event_id']} • "
                     + adventure.tr(lang, "wznów także po restarcie", "resume even after restart"))
    return illustrate_event(embed, state)


async def current_state(interaction, state):
    """Check live access and version after acknowledging the click, even after restart."""
    current = await asyncio.to_thread(adventure.load_run, state['event_id'])
    if not await asyncio.to_thread(can_manage, current['nation_id'], interaction.user.id):
        raise ValueError(adventure.tr(current['lang'],
            'Decyzję podejmuje właściciel lub coop tego państwa.',
            "Only this nation's owner or co-op members can decide."))
    if current['version'] != state['version'] or current['resolved']:
        await send_event(interaction.followup.send, current, view=EventView(current), ephemeral=True,
                         content=adventure.tr(current['lang'],
                             'Ten etap już się zmienił. Pokazuję aktualny stan; nie naliczono dodatkowej decyzji.',
                             'This stage has already changed. Here is the current state; no extra decision was charged.'))
        return None
    return current


async def respond(interaction, state, choice=None, answer=None, *, deferred=False):
    if not deferred:
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        state = await current_state(interaction, state)
        if state is None:
            return
        with i18n.using_language(state['lang']):
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
    await send_event(interaction.followup.send,updated,view=EventView(updated),ephemeral=True)
    await retire_buttons(interaction)


async def retire_buttons(interaction):
    message = getattr(interaction, 'message', None)
    if message is not None:
        try:
            await message.edit(view=None)
        except discord.HTTPException:
            # The decision is committed and the new message is already delivered.
            # A surviving old button safely restores current_state on the next click.
            logging.info('Could not remove old event buttons from message %s', message.id)


class EventAction(discord.ui.DynamicItem[discord.ui.Button],
                  template=r'wargame:event:(?P<event_id>\d+):(?P<version>\d+):(?P<action>[012]|custom|retry)'):
    """Route durable component IDs without relying on an in-memory View instance."""
    def __init__(self, event_id, version, action, label, style=discord.ButtonStyle.secondary):
        self.event_id, self.version, self.action = event_id, version, action
        super().__init__(discord.ui.Button(label=label, style=style,
                         custom_id=f'wargame:event:{event_id}:{version}:{action}'))

    @property
    def label(self):
        return self.item.label

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['event_id']), int(match['version']), match['action'], item.label, item.style)

    async def callback(self, interaction):
        # No database or AI work before the initial Discord response.
        locale = getattr(getattr(interaction, 'locale', None), 'value', '') or ''
        state = dict(event_id=self.event_id, version=self.version,
                     lang='en' if self.label in ('Custom response', 'Reload choices')
                     or (self.action in ('0', '1', '2') and locale.startswith('en')) else 'pl')
        if self.action == 'custom':
            await interaction.response.send_modal(CustomAnswer(state))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.action == 'retry':
                await retry_response(interaction, state, deferred=True)
            else:
                await respond(interaction, state, choice=int(self.action), deferred=True)
        except Exception:
            logging.exception('Event button failed for %s', self.event_id)
            await interaction.followup.send(adventure.tr(state['lang'],
                'Nie udało się obsłużyć kliknięcia. Otwórz aktualny stan przez /event play.',
                'Could not process the click. Open the current state with /event play.'),ephemeral=True)


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
        super().__init__(timeout=None)
        self.state = state
        if state["resolved"]:
            return
        retry=adventure.needs_scene_retry(state)
        for index in range(0 if retry else 3):
            self.add_item(EventAction(state['event_id'], state['version'], str(index), str(index + 1), discord.ButtonStyle.primary))
        self.add_item(EventAction(state['event_id'], state['version'], 'custom',
                                 adventure.tr(state["lang"], "Własna odpowiedź", "Custom response")))
        if retry:
            self.add_item(EventAction(state['event_id'], state['version'], 'retry',
                                     adventure.tr(state['lang'],'Załaduj odpowiedzi ponownie','Reload choices'),
                                     discord.ButtonStyle.primary))


async def retry_response(interaction, state, *, deferred=False):
    if not deferred:
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        state = await current_state(interaction, state)
        if state is None:
            return
        with i18n.using_language(state['lang']):
            updated=await adventure.retry_scene(state['event_id'],state['version'],interaction.user.id)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
        return
    except Exception:
        logging.exception('Failed to regenerate event %s choices',state['event_id'])
        await interaction.followup.send(adventure.tr(state['lang'],
            'Nie udało się zapisać nowych wyborów. Użyj /event play.',
            'Could not save new choices. Use /event play.'),ephemeral=True)
        return
    await send_event(interaction.followup.send,updated,view=EventView(updated),ephemeral=True)
    await retire_buttons(interaction)


async def send_event(sender,state,*,public=False,editing=False,**kwargs):
    """Each message gets a fresh attachment, including DMs and resumed decisions."""
    embed=render_public_event(state) if public else render_event(state)
    file=event_media.attachment(state['event_id'])
    try:
        if editing:kwargs['attachments']=[file] if file else []
        elif file:kwargs['file']=file
        return await sender(embed=embed,allowed_mentions=discord.AllowedMentions.none(),**kwargs)
    finally:
        if file:file.close()
