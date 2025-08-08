import random
import traceback
from meta_ai_api import MetaAI

with open("censorship.txt", "r") as f:
  curses = f.readlines()
list(curses)

def create_gama_instance():
	"""
	Initializes and primes a MetaAI instance with a random
	chunk of context from a file to roleplay as 'Gama'.
	This is a blocking function and should be run in a thread.
	"""
	global lines
	FILE_PATH = 'filtered_messages.txt'
	MAX_CHARS = 34000
	MIN_START_LINE = 1
	MAX_START_LINE = 4500
	USERNAME = "Gama"
	msg_one = "No context loaded."
	msg_two = "Context file may be missing or empty."
	try:
		with open(FILE_PATH, 'r', encoding='utf-8', errors='replace') as f:
			all_lines = f.readlines()
			filtered_lines = [line for line in all_lines if "http" not in line]

		start_idx = random.randint(MIN_START_LINE, min(MAX_START_LINE, len(filtered_lines))) - 1
		selected_text = ""
		curr_idx = start_idx

		while curr_idx < len(filtered_lines) and len(selected_text) < MAX_CHARS:
			line = filtered_lines[curr_idx]
			if len(selected_text) + len(line) <= MAX_CHARS:
				selected_text += line
			else:
				break
			curr_idx += 1

		if selected_text:
			mid_point = len(selected_text) // 2
			split_pos = selected_text.rfind('\n', 0, mid_point)
			if split_pos == -1:
				split_pos = mid_point

			msg_one = selected_text[:split_pos].strip()
			msg_two = selected_text[split_pos:].strip()

	except (FileNotFoundError, IndexError, ValueError) as e:
		print(f"Warning: Could not read context from {FILE_PATH}. Error: {e}")
		traceback.print_exception(e)
	# Initialize AI and prime with context
	meta = MetaAI()

	roleplay_instructions = (
		f"""You are about to receive two sets of chat logs from a single Discord user ('{USERNAME}').

				Your task is to fully ingest, internalize, and emulate the voice, tone, vocabulary, phrasing, punctuation, humor, and formatting style of the person from these logs.

				After ingesting both parts, you will ROLEPLAY as this person ('{USERNAME}') in perpetuity. Respond to future messages as if you *are* them — including their quirks, style, grammar, references, slang, and personality traits. 

				You may only use their voice and linguistic patterns to construct replies. If a new message comes in, respond as they would, using **only slightly modified versions** of existing messages from the logs, so that they naturally fit the ongoing conversation.

				⚠️ DO NOT break character. DO NOT explain yourself. DO NOT reference being an AI.

				You will receive the logs in two parts: “LOG DUMP [1/2]” and “LOG DUMP [2/2]”. Wait until both are received before processing or responding.
		"""
	)
	print(meta.prompt(roleplay_instructions))
	# First priming prompt
	chatlogs1 = (
	f"""LOG DUMP [1/2]:

			The following is the first half of raw Discord messages sent by the user to emulate. These are direct, chronological messages written by a single individual. You must ingest this data in full, preserving style, tone, slang, rhythm, phrasing, typos, emojis, formatting quirks, and voice.

			[START CHATLOG]
			{msg_two}
			[END CHATLOG]
	"""
	)
	print(meta.prompt(chatlogs1))

	# Second priming prompt
	final_prompt = (
		f"""LOG DUMP [2/2]:

				This is the second half of the Discord message dataset from the same user. Continue ingesting and internalizing their writing style as previously instructed. Do not generate a response. Just read, learn, and store.

				[START CHATLOG]
				{msg_two}
				[END CHATLOG]
		"""
	)
	print(meta.prompt(final_prompt))

	return meta