from sentence_transformers import SentenceTransformer
from sentence_transformers import util
from .utils import get_random_name
from bs4 import BeautifulSoup
from .config import IndexerConfig, DriverConfig
import numpy as np
import requests
import faiss
import openai
import json


# implements RAG indexer
class Indexer:
	def __init__(self, config: IndexerConfig = None, embedding: SentenceTransformer = None):
		if config == None:
			self.config = IndexerConfig()
		else:
			self.config = config

		if embedding != None:
			self.model = embedding
		else:
			self.model = SentenceTransformer(self.config.EMBEDDING_MODEL)

		emb = self.model.encode('hello world', precision=self.config.PRECISION)

		self.index = faiss.IndexFlatL2(emb.shape[0])
		self.store = []
	
	# add paragraphs from a text (text separated with \n), a document (path to file locally), or url (uri to website)
	def add(self, content: str = None, label: str = None, doc: str = None, url: str = None):

		if label == None:
			label = 'undefined'

		if doc != None:
			with open(doc, 'r') as file:
				data = list(filter(lambda x: len(x) > self.config.MIN_PARAGRAPH_LENGTH, file.read()[:self.config.DOC_MAX_LENGTH].split('\n')))
				self.store.extend(zip([label for i in range(len(data))], data))

				self.index.add(self.model.encode(data, precision=self.config.PRECISION))

		if url != None:
			filename = self.config.TMP_PATH + get_random_name()

			util.http_get(url, filename)

			with open(filename) as file:
				soup = BeautifulSoup(file.read(), 'html.parser')

				data = list(filter(lambda x: len(x) > self.config.MIN_PARAGRAPH_LENGTH, soup.get_text()[:self.config.DOC_MAX_LENGTH].split('\n')))
				self.store.extend(zip([label for i in range(len(data))], data))

				self.index.add(self.model.encode(data, precision=self.config.PRECISION))

		if content != None:
			data = list(filter(lambda x: len(x) > self.config.MIN_PARAGRAPH_LENGTH, content[:self.config.DOC_MAX_LENGTH].split('\n')))
			self.store.extend(zip([label for i in range(len(data))], data))

			self.index.add(self.model.encode(data, precision=self.config.PRECISION))
				
	# retrieve information about a specific paragraph
	def retrieve(self, id: int) -> tuple:
		return self.store[id]

	# search for most similar paragraphs
	def search(self, query: str, label: str = None, top: int = 5) -> list:
		_, ids = self.index.search(np.array([self.model.encode(query)]), top)

		return ids[0].tolist()


# implements a template used for quering llm
class Templater:
	def __init__(self, msgs: list):
		self.system = ''
		self.prompt = ''

		for msg in msgs:
			if msg[0] == 'system':
				self.system += msg[1] + '\n'
			else:
				self.prompt += msg[1] + '\n'


class Driver:
	def __init__(self, config: DriverConfig = None):
		if config == None:
			self.config = DriverConfig()
		else:
			self.config = config
	
	# if __prompt is specified, it queries llm with just __prompt, ignoring the template
	# otherwise it uses the specified template and substitutes template arguments with **kargs
	def query(self, __prompt: str = None, template: Templater = None, url_token: str = None, llm_type: str = 'local', **kargs) -> str:
		system = None
		prompt = None

		if template != None and __prompt == None:
			system = template.system.format(**kargs)

		if template != None and __prompt == None:
			prompt = template.prompt.format(**kargs)
		else:
			prompt = __prompt

		params = {
			'model': self.config.LLM_MODEL,
			'prompt': prompt,
			'stream': False
		}

		if system != None:
			params.update({'system': system})

		if llm_type == 'openai':
			if url_token != None:
				api_key = url_token
			else:
				api_key = self.config.OPENAI_TOKEN

			response = (openai.OpenAI(api_key=api_key)).chat.completions.create(
				model="gpt-3.5-turbo-16k",
				messages=[
					{
						'role': 'system',
						'content': system
					},
					{
						'role': 'user',
						'content': prompt
					}
				]
			)

			return json.dumps({'response': response.choices[0].message.content, 'done': True}, ensure_ascii=False)
		else:
			if url_token != None:
				resp = requests.post(url_token, json=params)
			else:
				resp = requests.post(self.config.LLM_BASE_URL, json=params)

			content = resp.json()['response']
			return json.dumps({'response': content, 'done': True}, ensure_ascii=False)
	
	# stream query requests
	def squery(self, __prompt: str = None, template: Templater = None, url_token: str = None, llm_type: str = 'local', **kargs) -> str:
		system = None
		prompt = None

		if template != None and __prompt == None:
			system = template.system.format(**kargs)

		if template != None and __prompt == None:
			prompt = template.prompt.format(**kargs)
		else:
			prompt = __prompt

		params = {
			'model': self.config.LLM_MODEL,
			'prompt': prompt,
			'stream': True
		}

		if system != None:
			params.update({'system': system})

		if llm_type == 'openai':
			if url_token != None:
				api_key = url_token
			else:
				api_key = self.config.OPENAI_TOKEN

			response = (openai.OpenAI(api_key=api_key)).chat.completions.create(
				model="gpt-3.5-turbo-16k",
				messages=[
					{
						'role': 'system',
						'content': system
					},
					{
						'role': 'user',
						'content': prompt
					}
				],
				stream=True
			)

			for chunk in response:
				if chunk.choices[0].delta.content == None:
					yield json.dumps({'response': '', 'done': True}, ensure_ascii=False)
				else:
					yield json.dumps({'response': chunk.choices[0].delta.content, 'done': False}, ensure_ascii=False)
		else:
			if url_token != None:
				resp = requests.post(url_token, json=params, stream=True)
			else:
				resp = requests.post(self.config.LLM_BASE_URL, json=params, stream=True)

			for data in resp.iter_lines():
				parsed = json.loads(data)
				yield json.dumps({'response': parsed['response'], 'done': parsed['done']}, ensure_ascii=False)
	
	# asynchronous query requests
	async def aquery(self, __prompt: str = None, template: Templater = None, url_token: str = None, async_requests = None, llm_type: str = 'local', **kargs) -> str:
		system = None
		prompt = None

		if template != None and __prompt == None:
			system = template.system.format(**kargs)

		if template != None and __prompt == None:
			prompt = template.prompt.format(**kargs)
		else:
			prompt = __prompt

		params = {
			'model': self.config.LLM_MODEL,
			'prompt': prompt,
			'stream': False
		}

		if system != None:
			params.update({'system': system})

		if llm_type == 'openai':
			if url_token != None:
				api_key = url_token
			else:
				api_key = self.config.OPENAI_TOKEN

			response = await (openai.AsyncOpenAI(api_key=api_key)).chat.completions.create(
				model="gpt-3.5-turbo-16k",
				messages=[
					{
						'role': 'system',
						'content': system
					},
					{
						'role': 'user',
						'content': prompt
					}
				]
			)

			return json.dumps({'response': response.choices[0].message.content, 'done': True}, ensure_ascii=False)
		else:
			if url_token != None:
				resp = await async_requests.post(url_token, json=params)
			else:
				resp = await async_requests.post(self.config.LLM_BASE_URL, json=params)

			content = (await resp.json())['response']
			return json.dumps({'response': content, 'done': True}, ensure_ascii=False)
