#Made by Alyssa Cote, contact at acote.sec@gmail.com if needed. May need to send multiple emails lmao

from collections import Counter
import json
from pathlib import Path
import random
from typing import Literal
import requests
import math
from urllib.parse import urljoin
from enum import Enum, auto
import hashlib

from z3 import Solver, IntVector, Distinct, Or, Sum, Bool, sat, If

DEBUG = False
fairness = 10 #Will generate this number of sets of two teams with randomly scrambled players, then pick the closest skill match. Higher numbers means more "perfectly" matched teams but less variation

ranksInput = {
	"u":0,
	"ur":0,
	"unranked":0,
	"i":1,
	"iron":1,
	"b":2, 
	"bronze":2,
	"s":3, 
	"silver":3,
	"g":4, 
	"gold":4,
	"p":5,
	"plat":5,
	"platinum":5, 
	"e":6, 
	"emerald":6,
	"d":7, 
	"diamond":7,
	"m":8, 
	"master":8,
	"gr":9, 
	"grandmaster":9,
	"grand master":9,
	"c":10,
	"challenger":10
}

ranksDisplay = {
	"0":"Unranked",
	"1":"Iron ", 
	"2":"Bronze ",
	"3":"Silver ",
	"4":"Gold ",
	"5":"Platinum ", 
	"6":"Emerald ",
	"7":"Diamond ",
	"8":"Master ",
	"9":"Grandmaster ",
	"10":"Challenger "
}

def strike(text):
    return ''.join([u'\u0336{}'.format(c) for c in text])

def scrambleAgents(agents):
	scrambledAgents = []
	while(len(agents) > 0):
		randNum = random.randrange(1,len(agents)) if len(agents) > 1 else 1 #randrange between 1 and 1 will not return 1 but a fatal error, so just set it to 1 instead
		scrambledAgents.append(agents.pop(randNum-1))
	return scrambledAgents

class ChampTag(Enum):
	MARKSMAN = "Marksman"
	TANK = "Tank"
	FIGHTER = "Fighter"
	SUPPORT = "Support"
	ASSASSIN = "Assassin"
	MAGE = "Mage"

class APIEndpoint(Enum):
	VERSION_LIST = "versions.json"

class CDNEndpoint(Enum):
	CHAMPION_LIST = "champion.json"
	CHAMPION_QUERY = "champion"

class DataDragonCache:
	"""
	A local JSON 'cache' to avoid spamming the endpoint(s).
	"""
	def __init__(self):
		self.cache_dir = Path("__dd_cache")
		if not self.cache_dir.exists():
			self.cache_dir.mkdir(exist_ok=True)
	
	def get_or_request(self, key: tuple[str], req: str):
		"""
		Query a data file by the parts that would make up it's url
		"""
		f = self.cache_dir / hashlib.md5("\x00".join(key).encode('utf-8')).hexdigest()
		if f.exists():
			return json.loads(f.read_text())
		else:
			response = requests.get(req).json()
			f.write_text(json.dumps(response))
			return response
	
	def set_version_data(self, version: int | Literal['latest'], req: str):
		version_json = self.cache_dir / "versions.json"
		if not version_json.exists():
			versions: list[str] = requests.get(req).json()
			version_json.write_text(json.dumps(versions))
		else:
			versions: list[str] = json.loads(version_json.read_text())
		if version == "latest":
			self.version = versions[0]
		else:
			self.version = versions[version]
		self.cache_dir = self.cache_dir / self.version.replace(".", "_")
		if not self.cache_dir.exists():
			self.cache_dir.mkdir(exist_ok=True)
		return self.version



class ConstraintChampionSampler:
	def __init__(self, champ_data: dict[str, dict]):
		self.champs = list(champ_data.keys())
		self.champ_to_tags = {
			name: set(ChampTag(t) for t in info['tags']) for name, info in champ_data.items()
		}
		self.tag_to_indexed_flags = self._build_tag_table()

	def _build_tag_table(self):
		"""
		Builds a dict: tag -> [bool list], where bool_list[i] = champ[i] has the tag
        """
		tag_map = {}
		for i, champ in enumerate(self.champs):
			for tag in self.champ_to_tags[champ]:
				tag_map.setdefault(tag, []).append(i)
		return tag_map
	
	def solve(self, tag_targets: dict[str, ChampTag], max_choices: int, num_slots: int = 10):
		solver = Solver()
		indices = IntVector("champ", num_slots)

		# Constraint 1: champions must be in valid range
		for i in range(num_slots):
			solver.add(indices[i] >= 0, indices[i] < len(self.champs))

		# Constraint 2: all indices must be distinct
		solver.add(Distinct(indices))

		# Constraint 3: tag coverage
		for tag, required_count in tag_targets.items():
			if tag not in self.tag_to_indexed_flags:
				continue

			tag_champ_indices = self.tag_to_indexed_flags[tag]

			tag_presence_bools = []
			for i in range(num_slots):
				tag_match = Or([indices[i] == champ_idx for champ_idx in tag_champ_indices])
				tag_presence_bools.append(If(tag_match, 1, 0))  # Z3 integer conditional

			solver.add(Sum(tag_presence_bools) == required_count)

		models = []
		while len(models) < max_choices and solver.check() == sat:
			model = solver.model()
			chosen_indices = [model.evaluate(idx).as_long() for idx in indices]
			models.append([self.champs[i] for i in chosen_indices])
			# Don't allow this solution for next iteration
			solver.add(Or([v != model.evaluate(v) for v in indices]))
		return models

class DataDragon:
	"""
	Wrapper around the RIOT data dragon centralized asset cdn
	- https://developer.riotgames.com/docs/lol#data-dragon

	Provides an interface to the aram champ chooser
	"""
	BASE_URL = "https://ddragon.leagueoflegends.com/"
	API_URL = urljoin(BASE_URL, "api/")

	def __init__(self, lang = "en_US", version: int | Literal["latest"] = "latest"):
		self.lang = lang
		self.cache = DataDragonCache()
		self.version = self.cache.set_version_data(version, urljoin(DataDragon.API_URL, APIEndpoint.VERSION_LIST.value))
		self.cdn_url = urljoin(DataDragon.BASE_URL, f"cdn/{self.version}/data/{self.lang}/")
	
	def get_champion_list(self, role_distribution: dict[ChampTag, int], max_choices: int = 100) -> list[str]:
		champ_data = self._cdn_request(CDNEndpoint.CHAMPION_LIST)['data']
		sampler = ConstraintChampionSampler(champ_data)
		champs = sampler.solve(role_distribution, max_choices)
		self.validate_solutions(champs, champ_data, role_distribution)
		return random.choice(champs)
	
	def validate_solutions(self, champ_name_lists: list[list[str]], champ_data: dict[str, dict], tag_targets: dict[ChampTag, int]) -> bool:
		"""
		Validates whether the provided champion lists satisfy the given tag distribution.
		"""
		for champ_names in champ_name_lists:
			tag_counter = Counter()
			for name in champ_names:
				if name not in champ_data:
					raise ValueError(f"Champion '{name}' not found in provided data.")
				tag_counter.update(ChampTag(t) for t in champ_data[name]["tags"])
			for tag, required_count in tag_targets.items():
				actual_count = tag_counter.get(tag, 0)
				assert actual_count == required_count, f"Not enough or too many {tag}: need {required_count}, got {actual_count}"
			assert len(set(champ_names)) == len(champ_names), "Duplicate champions found."
			assert len(champ_names) == 10, f"Expected 10 champions, got {len(champ_names)}"
	
	def _cdn_request(self, endpoint: CDNEndpoint, subpath: str = None):
		full_endpoint = endpoint.value + f"/{subpath}" if subpath is not None else endpoint.value
		url = urljoin(self.cdn_url, full_endpoint)
		return self.cache.get_or_request((endpoint.value, subpath if subpath is not None else ""), url)

	def _api_request(self, endpoint: APIEndpoint):
		url = urljoin(DataDragon.API_URL, endpoint.value)
		return self.cache.get_or_request((endpoint.value,), url)

class Agent:
	def __init__(self, summonerName, rankString, level):
		self.summonerName = summonerName
		self.rankString = rankString
		self.skillScore = 0
		self.level = level

	def __str__(self):
		return "__Agent__\nsummonerName `"+self.summonerName+"`\nrankString `"+self.rankString+"`\nskillScore `"+str(self.skillScore)+"`\nlevel `"+str(self.level)+"`"

class Team:
	def __init__(self, players):
		self.players = players
		self.sumSkill = self.calcSumSkill(players)

	def calcSumSkill(self, players):
		try:
			sumSkill = 0
			for player in players:
				sumSkill = sumSkill + player.level
			return sumSkill
		except:
			return "ERROR: Team constructor was not given a list of Agent objects"
		
	def genShortList(self):
		shortlist = ""
		for player in self.players:
			shortlist = shortlist+player.summonerName+"\n"
		return shortlist
		
	def __str__(self):
		playersString = ""
		for player in self.players:
			playersString = playersString+player.summonerName+" | LVL "+str(player.level)+" | "+player.rankString+"\n"
		return "__Team__\nsumSkill `"+str(self.sumSkill)+"`\nplayers `\n"+playersString+"`"
	
class TeamSet:
	def __init__(self, team1, team2):
		self.team1 = team1
		self.team2 = team2
		self.skillDiff = self.calcSkillDiff(team1, team2)

	def calcSkillDiff(self, team1, team2):
		try:
			return abs(team1.sumSkill - team2.sumSkill)
		except:
			return 4444
		
	def __str__(self):
		team1ShortList = self.team1.genShortList()
		team2ShortList = self.team2.genShortList()
		return "__TeamSet__\nskillDiff `"+str(self.skillDiff)+"`\nteam1 `\n"+team1ShortList+"`\nteam2 `\n"+team2ShortList+"`"

def aram_champ_selector():
	dd = DataDragon()
	roles = {
		ChampTag.MARKSMAN: 4,
		ChampTag.ASSASSIN: 2,
		ChampTag.MAGE: 3,
		ChampTag.FIGHTER: 2,
		ChampTag.SUPPORT: 2,
		ChampTag.TANK: 4
	}
	champ_list = dd.get_champion_list(roles, 100)
	print(champ_list)

def form_balanced_teams():
	#Lets ask how rough the teams generated should be

	fairness = int(input("\nEnter a number for how roughly skilled the teams should be, with higher numbers being more fair\nIf regenerations keep giving the same teams, decrease fairness number\nRecommended fairness:\nRough - Player count times 1 or 2\nFair - Player count times 3 to 5\n\"Perfect\" - Fairness of 100+\nEnter fairness: "))

	#First things first, we need all of the players and their information

	try:
		newAgent = input("\nEnter player information as such `summoner name, ranked rank, level`\nExample:\n`daisy go bonk, platinum 4, 522`: ")
		#summoner name, rank, level
		agents = []
		while(not newAgent == "q"):
			try:
				if DEBUG: print("New Agent:")
				newAgentArgs = newAgent.split(",")
				for i, arg in enumerate(newAgentArgs): #small san loop in case someone inputs `summoner name, rank, level` instead of `summoner name,rank,level`
					if arg[0] == " ":
						newAgentArgs[i] = arg[1:]
				if DEBUG: print(newAgentArgs)
				summonerName = newAgentArgs[0]
				rankDiv = newAgentArgs[1][:-2].lower() #Example: "Iron 3" becomes "iron"
				rankSubDiv = int(newAgentArgs[1][-1:]) #Example "Iron 3" becomes "3"
				rankString = ranksDisplay[str(ranksInput[rankDiv])]+str(rankSubDiv)
				level = int(newAgentArgs[2])
				createdAgent = Agent(summonerName,rankString,level)
				if DEBUG: print(createdAgent)
				agents.append(createdAgent)
			except:
				print("\n\nERROR: Try again please!\n\n")
			newAgent = input("Enter new player (enter q to end input): ")
	except Exception as err:
		print(f"Unexpected {err=}, {type(err)=}")
		print("FATAL ERROR: Problem in player data entering")
		exit()

	#Now that we have our players, lets scramble the list then take the first half and put them in a new team, this creates a team set
	#We're going to do this an amount of times equal to the fairness setting to create that many potential team sets

	try:
		masterTeamsets = []
		for i in range(0, fairness):
			team1 = scrambleAgents(agents.copy()) #copy is necessary or the operations will simply exhaust the original agents list
			team2 = []
			for t in range(0, math.floor(len(team1)/2)): #find the playercount, take half (leaving the odd man on team1 if there is one), put in team2
				team2.append(team1.pop(0))
			teamset = TeamSet(Team(team1),Team(team2)) #Now that we have our teams, lets make them into proper objects which will auto calculate sum intra team skill and inter team skill diff
			masterTeamsets.append(teamset)
			if DEBUG: print(teamset)
	except Exception as err:
		print(f"Unexpected {err=}, {type(err)=}")
		print("FATAL ERROR: Problem in team or team set generations")
		exit()

	#Now that we have all of our potential team sets, lets find the best one. The lowest difference in team skill scores should do the trick.

	try:
		curBestSet = TeamSet('','') #Incorrectly constructed teamset leaves a default skill diff of 4444 which will never be worse than a real potential set
		for teamset in masterTeamsets:
			if(curBestSet.skillDiff > teamset.skillDiff):
				curBestSet = teamset
	except Exception as err:
		print(f"Unexpected {err=}, {type(err)=}")
		print("FATAL ERROR: Problem in best team set calculation")
		exit()

	#Lets present our chosen final set!

	try:
		print("\n\n========== Generated Teams ==========\nSkill Difference:"+str(curBestSet.skillDiff))
		print("\n  --- Team 1 ---")
		for player in curBestSet.team1.players:
			print(player.summonerName+" | LVL "+str(player.level)+" | "+player.rankString)
		print("\n  --- Team 2 ---")
		for player in curBestSet.team2.players:
			print(player.summonerName+" | LVL "+str(player.level)+" | "+player.rankString)
		print("\n")
	except Exception as err:
		print(f"Unexpected {err=}, {type(err)=}")
		print("FATAL ERROR: Problem in final set display")
		exit()
	
	print("Thank you for using Alyssa's League Custom Game Team Scrambler!")


"""
action = ""
while(not action == "exit"):
	try:
		action = input("\n\nEnter whether you would like to \'next\' \'incapacitate [turn#]\' \'heal [turn#]\' \'remove [turn#]\' \'add\' \'list\' \'skip [toTurn#]\' \'exit\': ")
		args = action.split(" ")
		if DEBUG:
			print(args[0])
		match args[0]:
			case "reroll":
				print("hi")	
			case "gen aram champs":
				print("hi2")
	except:
		print("\n\nERROR: Try again please!\n\n")"""

aram_champ_selector()
form_balanced_teams()