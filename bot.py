 
import discord
from discord.ext import commands, tasks

import os
import logging
import asyncio
import time
import traceback
from typing import Dict, Any, AsyncGenerator, Optional, List, Generator
from collections import deque

from meta_ai_api import MetaAI
from dotenv import load_dotenv


from custom_prompt import create_gama_instance, curses


def setup_logging():
	"""Configures logging for the application."""
	
	
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s [%(levelname)s] %(message)s",
		handlers=[
			logging.StreamHandler()
		]
	)







class BotConfig:
	"""Stores bot constants."""
	DISCORD_MSG_CHAR_LIMIT = 1000
	UPDATE_INTERVAL_SECONDS = 0.7
	AI_INACTIVITY_THRESHOLD = 15 * 60
	AI_MAX_ERRORS = 3
	AI_SORRY_PHRASE = "Sorry, I can’t help you" 
	AI_MAX_SORRY_RESPONSES = 3 
	REQUEST_LIMIT_PER_MINUTE = 8
	RATE_LIMIT_COOLDOWN_SECONDS = 15
	RATE_LIMIT_MESSAGE = "hey hey, are you trolling me? Give me a second to chill out and try again."


class CustomPrompts:
	"""Module for custom AI prompts."""
	@staticmethod
	def get_restart_prompt() -> str:
		"""Gets system-check prompt."""
		return "Confirm you are operational by responding with 'System OK'."

	@staticmethod
	def format_user_prompt(prompt: str) -> str:
		"""Formats user prompt."""
		return f"""Reminder: You are roleplaying as the Discord user from the ingested logs. You must ONLY speak as they would. Pull directly from their past messages or synthesize new replies by subtly remixing and rephrasing real content from the logs.

						Maintain:
						- Their tone, style, attitude, spelling, slang, and punctuation.
						- Their formatting habits (emojis, caps, line breaks, etc.)
						- Their persona (sarcastic, kind, chaotic, formal, whatever it is)

						Do NOT break character. Do NOT act like an AI. You are this person now.

						Now respond to the user who mentioned you as they would:
						[START DISCORD USER PROMPT]
						{prompt}
						[END DISCORD USER PROMPT]
						"""


class MetaAIManager:
	"""Manages MetaAI session."""
	def __init__(self, inactivity_threshold: int, max_errors: int):
		self.ai_instance: Optional[MetaAI] = None
		self.lock = asyncio.Lock()
		self.error_count = 0
		self.sorry_response_count = 0 
		self.max_errors = max_errors
		self.last_activity_time: float = time.time()
		self.inactivity_threshold = inactivity_threshold

	async def start_session(self) -> bool:
		"""Initializes MetaAI session."""
		logging.info("Initializing Meta AI session...")
		async with self.lock:
			if self.ai_instance:
				del self.ai_instance
				self.ai_instance = None

			try:
				if os.getenv("DOCKER_ENV"):
					logging.info("Docker env detected. Using no-sandbox args.")
					
				
				self.ai_instance = await asyncio.to_thread(
					create_gama_instance)
				if not self.ai_instance:
					raise RuntimeError("Instance creation returned None")

				def consume_stream(gen: Generator):
					for _ in gen: pass

				response_gen = self.ai_instance.prompt(
					message=CustomPrompts.get_restart_prompt(),
					stream=True
				)
				await asyncio.to_thread(consume_stream, response_gen)

				logging.info("Meta AI session started successfully.")
				self.error_count = 0
				self.sorry_response_count = 0 
				self.last_activity_time = time.time()
				return True
			except Exception as e:
				logging.error(f"Failed to start Meta AI session: {e}")
				traceback.print_exception(type(e), e, e.__traceback__)
				self.ai_instance = None
				return False

	async def restart_session(self) -> bool:
		"""Public method to restart session."""
		logging.warning("Restarting Meta AI session...")
		return await self.start_session()

	async def get_response_stream(
		self, prompt: str
	) -> AsyncGenerator[Dict[str, Any], None]:
		"""Gets streamed response from AI."""
		self.last_activity_time = time.time()
		if not self.ai_instance:
			logging.error("AI not initialized. Restarting...")
			if not await self.restart_session():
				yield {"error": "AI session could not be restarted."}
				return

		def _get_next_chunk(generator: Generator) -> Any:
			try:
				return next(generator)
			except StopIteration:
				return None

		async with self.lock:
			logging.info(f"New prompt: '{prompt[:75]}...'")
			try:
				response_generator = self.ai_instance.prompt(
					message=prompt, stream=True
				)
				loop = asyncio.get_running_loop()

				while True:
					chunk = await loop.run_in_executor(
						None, _get_next_chunk, response_generator
					)
					if chunk is None:
						break
					yield chunk

				self.error_count = 0
			except Exception as e:
				logging.error(f"Meta AI API error: {e}")
				traceback.print_exception(type(e), e, e.__traceback__)
				self.error_count += 1
				yield {"error": str(e)}
				if self.error_count >= self.max_errors:
					logging.warning("Max errors reached. Auto-restarting.")
					await self.restart_session()

	@tasks.loop(minutes=1.0)
	async def check_inactivity(self):
		"""Checks for inactivity."""
		try:
			is_inactive = (time.time() - self.last_activity_time) > self.inactivity_threshold
			is_free = not self.lock.locked()

			if is_inactive and is_free:
				logging.info(f"Inactive > {self.inactivity_threshold / 60:.0f}m. Restarting.")
				await self.restart_session()
		except Exception as e:
			logging.error(f"Error in check_inactivity task: {e}")
			traceback.print_exception(type(e), e, e.__traceback__)







class DiscordMessageHandler:
	"""Handles a bot response."""
	def __init__(self, bot: "MetaDiscordBot", message: discord.Message, prompt: str):
		self.bot = bot
		self.message = message
		self.prompt = prompt
		self.bot_message: Optional[discord.Message] = None

	def _truncate(self, content: str) -> str:
		"""Truncates long messages."""
		if len(content) > BotConfig.DISCORD_MSG_CHAR_LIMIT:
			return content[:BotConfig.DISCORD_MSG_CHAR_LIMIT - 4] + "..."
		return content

	def _format_sources(self, sources: Optional[List[Dict[str, str]]]) -> str:
		"""Formats sources for display."""
		if not sources:
			return ""
		source_list = [f"{i}. <{s.get('link', '#')}>" for i, s in enumerate(sources[:5], 1)]
		return "\n\n**Sources:**\n" + "\n".join(source_list)

	async def _handle_stream_error(self, error_chunk: Dict[str, Any]):
		"""Handles an error from the AI stream."""
		err_msg = f"An API error occurred: {error_chunk['error']}"
		logging.error(err_msg)
		await self.bot_message.edit(content=self._truncate(err_msg))

	async def _update_streamed_message(self, chunk: Dict[str, Any], last_edit_time: float, edited_content: str) -> tuple[float, str]:
		"""Updates the Discord message with new stream content."""
		content_buffer = chunk.get("message", "")
		now = time.time()
		
		if (now - last_edit_time > BotConfig.UPDATE_INTERVAL_SECONDS and content_buffer != edited_content):
			display_content = self._truncate(content_buffer) + "..."
			await self.bot_message.edit(content=display_content)
			return now, content_buffer
		
		return last_edit_time, edited_content
	
	async def _handle_sorry_limit_restart(self):
		"""Handles the auto-restart when the 'sorry' limit is reached."""
		logging.warning(f"AI 'sorry' limit of {BotConfig.AI_MAX_SORRY_RESPONSES} reached. Triggering restart.")
		await self.bot_message.edit(content="I'm going to restart myself really quick. Give me 10 seconds.")
		await self.bot.ai_manager.restart_session()
		
	async def _finalize_message(self, last_chunk: Dict[str, Any]):
		"""Edits the final message with the complete response and sources."""
		final_message = last_chunk.get("message", "").strip()

		if BotConfig.AI_SORRY_PHRASE in final_message:
			self.bot.ai_manager.sorry_response_count += 1
			logging.warning(f"AI 'sorry' response detected. Count: {self.bot.ai_manager.sorry_response_count}")

			if self.bot.ai_manager.sorry_response_count >= BotConfig.AI_MAX_SORRY_RESPONSES:
				await self._handle_sorry_limit_restart()
				return
		elif self.bot.ai_manager.sorry_response_count > 0:
			logging.info("Resetting 'sorry' response counter.")
			self.bot.ai_manager.sorry_response_count = 0
		
		if not final_message and not last_chunk:
			final_content = "<No response received>"
		else:
			sources_formatted = self._format_sources(last_chunk.get("sources"))
			final_content = self._truncate(final_message + sources_formatted)

		await self.bot_message.edit(content=final_content)

	async def process_response(self):
		"""Main handler for the AI response lifecycle."""
		global curses
		try:
			for curse in curses:
				if curses:
					curse_set = {c.lower() for c in curses}
					
					new_prompt_parts = []
					current_word = ""

					
					for char in self.prompt:
						if char.isalpha():
							current_word += char
						else:
							
							if current_word:
								
								if current_word.lower() in curse_set and len(current_word) > 2:
									censored = current_word[:2] + '-' * (len(current_word) - 2)
									new_prompt_parts.append(censored)
								else:
									new_prompt_parts.append(current_word)
								current_word = ""
							new_prompt_parts.append(char)
					
					
					if current_word:
						if current_word.lower() in curse_set and len(current_word) > 2:
							censored = current_word[:2] + '-' * (len(current_word) - 2)
							new_prompt_parts.append(censored)
						else:
							new_prompt_parts.append(current_word)
					
					self.prompt = "".join(new_prompt_parts)

			
 
			self.bot_message = await self.message.channel.send("😈 Thinking...", reference=self.message)
			stream = self.bot.ai_manager.get_response_stream(
				CustomPrompts.format_user_prompt(self.prompt)
			)

			last_edit_time = time.time()
			edited_content = ""
			last_chunk = {}

			async with self.message.channel.typing():
				async for chunk in stream:
					if "error" in chunk:
						await self._handle_stream_error(chunk)
						return
					
					last_chunk = chunk
					last_edit_time, edited_content = await self._update_streamed_message(
						chunk, last_edit_time, edited_content
					)
			
			await self._finalize_message(last_chunk)

		except Exception as e:
			logging.error(f"Error in DiscordMessageHandler: {e}")
			traceback.print_exception(type(e), e, e.__traceback__)
			if self.bot_message:
				try:
					await self.bot_message.edit(content="A bot error occurred. Check logs.")
				except discord.errors.DiscordException as de:
					logging.error(f"Failed to edit error message: {de}")







class MetaDiscordBot(commands.Bot):
	"""The Discord Bot class."""
	def __init__(self, ai_manager: MetaAIManager):
		intents = discord.Intents.default()
		intents.message_content = True
		super().__init__(command_prefix="!", intents=intents)
		self.ai_manager = ai_manager
		self.request_timestamps: deque[float] = deque() # rate limit
		self.cooldown_until: float = 0.0 # rate limit

	async def setup_hook(self) -> None:
		"""Runs on bot setup."""
		await self.ai_manager.start_session()
		self.ai_manager.check_inactivity.start()
		logging.info("Setup hook complete. Tasks started.")

	async def on_ready(self) -> None:
		"""Runs when bot is ready."""
		logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
		logging.info("-" * 20)

	def _should_process_message(self, message: discord.Message) -> bool:
		"""Determines if a message is for the bot."""
		if message.author.bot:
			return False
		is_mentioned = self.user in message.mentions
		is_reply = (
			message.reference and
			message.reference.cached_message and
			message.reference.cached_message.author == self.user
		)
		return is_mentioned or is_reply

	async def on_message(self, message: discord.Message) -> None:
		"""Handles incoming messages."""
		await self.process_commands(message)

		if not self._should_process_message(message):
			return

		now = time.time()
		
		# check cooldown
		if now < self.cooldown_until:
			self.cooldown_until = now + BotConfig.RATE_LIMIT_COOLDOWN_SECONDS # reset timer
			await message.channel.send(
				BotConfig.RATE_LIMIT_MESSAGE,
				reference=message,
				delete_after=15
			)
			return

		# prune old reqs
		one_minute_ago = now - 60
		while self.request_timestamps and self.request_timestamps[0] < one_minute_ago:
			self.request_timestamps.popleft()
		
		# check limit
		if len(self.request_timestamps) >= BotConfig.REQUEST_LIMIT_PER_MINUTE:
			logging.warning("Rate limit hit. Triggering cooldown.")
			self.cooldown_until = now + BotConfig.RATE_LIMIT_COOLDOWN_SECONDS # start timer
			await message.channel.send(
				BotConfig.RATE_LIMIT_MESSAGE,
				reference=message,
				delete_after=15
			)
			return
		
		self.request_timestamps.append(now) # track request

		prompt = message.content.replace(f"<@{self.user.id}>", "").strip()
		if not prompt:
			await message.channel.send("Please provide a prompt.", reference=message, delete_after=10)
			return

		handler = DiscordMessageHandler(self, message, prompt)
		asyncio.create_task(handler.process_response())

	@commands.command(name="restart_ai", help="Restarts the AI session (Owner only).")
	@commands.is_owner()
	async def restart_ai_command(self, ctx: commands.Context):
		"""Manually restarts AI session."""
		await ctx.message.add_reaction("⏳")
		success = await self.ai_manager.restart_session()
		await ctx.message.remove_reaction("⏳", self.user)
		await ctx.message.add_reaction("✅" if success else "❌")
		if not success:
			await ctx.send("Failed to restart AI. Check logs.", delete_after=20)

	@restart_ai_command.error
	async def restart_ai_error(self, ctx: commands.Context, error: commands.CommandError):
		"""Handles errors for restart_ai command."""
		if isinstance(error, commands.NotOwner):
			await ctx.send("Permission denied.", delete_after=10)
		else:
			logging.error(f"Error in restart_ai command: {error}")
			traceback.print_exception(type(error), error, error.__traceback__)
		
		try:
			await ctx.message.delete(delay=10)
		except discord.errors.DiscordException:
			pass







def main():
	"""Main function to run the bot."""
	load_dotenv()
	setup_logging()

	TOKEN = os.getenv("DISCORD_TOKEN")
	if not TOKEN:
		logging.critical("CRITICAL: DISCORD_TOKEN not found in .env file.")
		return

	try:
		ai_manager = MetaAIManager(
			inactivity_threshold=BotConfig.AI_INACTIVITY_THRESHOLD,
			max_errors=BotConfig.AI_MAX_ERRORS
		)
		bot = MetaDiscordBot(ai_manager=ai_manager)
		bot.run(TOKEN)
	except discord.errors.LoginFailure:
		logging.critical("CRITICAL: Login failed. Check your DISCORD_TOKEN.")
	except Exception as e:
		logging.critical(f"CRITICAL: Bot failed to start: {e}")
		traceback.print_exception(type(e), e, e.__traceback__)


if __name__ == "__main__":
	main()