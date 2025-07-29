# leagueCustomGameTeamGenerator
Tries to semi-randomly create balanced teams for league custom games

Currently only evaluates skill based on player level.

Current use and output looks like this:
![alt text](https://i.imgur.com/RtQrR2N.png)

Plans to add:
- Aram champ roller
  - Will roll 1-3 champs per player, per team to give an easy and immediate roster to pick from
- Player tracking
  - Will remember players between generations and allow for adding and removing to reduce reconfiguration between games
- Rescrambling of teams
  - If the teams generated are not satisfactory
- Changing of fairness setting
 - So a restart is not required
- Skill evaluation based on ranked rank
  - To allow for more fair balancing
- Skill evaluation based on combination of ranked rank and level
  - To allow for EVEN more fair balancing
  - Potential to weight scores where higher ranks at lower levels are valued more and lower ranks at higher levels are valued less
