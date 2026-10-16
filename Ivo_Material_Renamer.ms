/*=============================================================
	Ivo Material Renamer for 3ds Max
	---------------------------------------------------------
	Gives meaningful names to scene materials by letting
	Claude AI "look" at their textures and the viewport.

	Usage:
	  1. Paste your API key and press "Save Key"
	  2. Press "ANALYZE & RENAME"
	  3. Review the proposed names and confirm

	Install: Scripting > Run Script > this file.
	Keep BOTH files (.ms and .py) in the SAME folder.

	Get a key at: https://console.anthropic.com/settings/keys
=============================================================*/

global IvoMatRenamerRoll
try (destroyDialog IvoMatRenamerRoll) catch()

(
	local thisFile = getSourceFileName()
	local thisDir  = getFilenamePath thisFile
	local pyFile   = thisDir + "Ivo_Material_Renamer.py"
	local iniFile  = (getDir #plugcfg) + "\\IvoMaterialRenamer.ini"

	local KEY_URL  = "https://console.anthropic.com/settings/keys"

	-- Register a toolbar macro so the tool can be found in
	-- Customize > Customize User Interface > Toolbars
	-- under Category: "Ivo Tools"
	try
	(
		macros.new "Ivo Tools" "Ivo_Material_Renamer" \
			"Rename materials with AI" "Mat Renamer" \
			("fileIn @\"" + thisFile + "\"")
	)
	catch()

	fn savedKey =
	(
		local k = getINISetting iniFile "API" "Key"
		if k == undefined then "" else k
	)

	fn maskKey k =
	(
		if k == undefined or k.count < 12 then ""
		else (substring k 1 8) + "..." + (substring k (k.count-3) 4)
	)

	rollout IvoMatRenamerRoll "Ivo Material Renamer" width:330 height:322
	(
		label lblT1 "Renames scene materials by recognizing" pos:[12,10]
		label lblT2 "their textures with AI." pos:[12,26]

		groupBox grpKey "Claude API key" pos:[8,48] width:314 height:104

		editText etKey "" pos:[16,70] width:298 height:20 text:""
		label lblStatus "" pos:[18,94] width:290 height:16

		button btnSave "Save Key" pos:[16,114] width:96 height:26
		button btnGetKey "Get a key" pos:[118,114] width:96 height:26
		button btnHelp "How to (help)" pos:[220,114] width:94 height:26

		groupBox grpAct "" pos:[8,160] width:314 height:126

		radiobuttons rbScope "" labels:#("Only on SELECTED objects", "All materials in the scene") pos:[18,174] columns:1 default:1

		checkbox chkTex "Also rename texture FILES on disk" pos:[18,212] checked:false
		label lblTexWarn "(selection mode only, files in scene folder)" pos:[36,228]

		button btnRun "ANALYZE & RENAME" pos:[16,248] width:298 height:30

		label lblFoot "Cost is roughly 1-3 cents per asset." pos:[12,294]

		fn refreshStatus =
		(
			local k = savedKey()
			if k == "" then
			(
				lblStatus.text = "No key saved yet."
				etKey.text = ""
			)
			else
			(
				lblStatus.text = "Saved key: " + (maskKey k)
				etKey.text = ""
			)
		)

		-- texture renaming is allowed only in "selected objects" mode
		fn syncTexOption =
		(
			local sel = (rbScope.state == 1)
			chkTex.enabled = sel
			lblTexWarn.enabled = sel
			if not sel do chkTex.checked = false
		)

		on rbScope changed s do syncTexOption()

		on IvoMatRenamerRoll open do
		(
			refreshStatus()
			syncTexOption()
			if not (doesFileExist pyFile) do
			(
				messageBox ("Missing file:\n" + pyFile + \
					"\n\nKeep both files (.ms and .py) in the same folder.") title:"Ivo Material Renamer"
			)
		)

		on btnSave pressed do
		(
			local k = trimLeft (trimRight etKey.text)
			if k == "" do
			(
				messageBox "Paste your key into the field first." title:"Ivo Material Renamer"
				return false
			)
			if (substring k 1 7) != "sk-ant-" do
			(
				if not (queryBox "This key does not start with 'sk-ant-'.\nSave it anyway?" title:"Ivo Material Renamer") do return false
			)
			setINISetting iniFile "API" "Key" k
			etKey.text = ""
			refreshStatus()
			messageBox "Key saved.\n\nIt is stored locally on this machine only." title:"Ivo Material Renamer"
		)

		on btnGetKey pressed do ( shellLaunch KEY_URL "" )

		on btnHelp pressed do
		(
			messageBox ("HOW TO GET AN API KEY\n\n" + \
				"1. Go to console.anthropic.com and create an account\n" + \
				"2. Settings > API keys > Create Key\n" + \
				"3. Copy the key IMMEDIATELY - it is shown only once\n" + \
				"4. Settings > Billing > Buy credits\n" + \
				"   ($5-10 is enough for hundreds of assets)\n" + \
				"5. Paste the key here and press Save Key\n\n" + \
				"The 'Get a key' button opens the page directly.\n\n" + \
				"Your key is stored locally in:\n" + iniFile) title:"How to get a key"
		)

		on btnRun pressed do
		(
			if (savedKey()) == "" do
			(
				messageBox "Save an API key first." title:"Ivo Material Renamer"
				return false
			)
			if not (doesFileExist pyFile) do
			(
				messageBox ("Missing: " + pyFile) title:"Ivo Material Renamer"
				return false
			)
			if chkTex.checked do
			(
				if not (queryBox ("You enabled RENAMING OF TEXTURE FILES on disk.\n\n" + \
					"Only files stored inside this scene's own folder\n" + \
					"will be touched - shared textures elsewhere are\n" + \
					"left alone.\n\n" + \
					"Other scenes that use the SAME local files would\n" + \
					"lose them. Save your scene first.\n\n" + \
					"Continue?") title:"Rename texture files?") do return false
			)
			setINISetting iniFile "API" "Scope" (rbScope.state as string)
			setINISetting iniFile "API" "RenameTextures" (if chkTex.checked then "1" else "0")
			python.ExecuteFile pyFile
		)
	)
	createDialog IvoMatRenamerRoll
)
