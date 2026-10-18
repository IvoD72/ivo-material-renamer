/*=============================================================
	Ivo Material Renamer for 3ds Max  v1.4.1
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
try (destroyDialog ::IvoMatRenamerRoll) catch()

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

	rollout IvoMatRenamerRoll "Ivo Material Renamer v1.4.1" width:330 height:390
	(
		label lblT1 "Renames scene materials by recognizing" pos:[12,10]
		label lblT2 "their textures with AI." pos:[12,26]

		groupBox grpKey "Claude API key" pos:[8,48] width:314 height:104

		editText etKey "" pos:[16,70] width:298 height:20 text:""
		label lblStatus "" pos:[18,94] width:290 height:16

		button btnSave "Save Key" pos:[16,114] width:96 height:26
		button btnGetKey "Get a key" pos:[118,114] width:96 height:26
		button btnHelp "How to (help)" pos:[220,114] width:94 height:26

		groupBox grpAct "" pos:[8,160] width:314 height:180

		radiobuttons rbScope "" labels:#("Only on SELECTED objects", "All materials in the scene") pos:[18,174] columns:1 default:1

		checkbox chkMaps "Also rename the maps (Map #...)" pos:[18,212] checked:true
		checkbox chkConv "Convert Bitmap to VRayBitmap / CoronaBitmap" pos:[18,230] checked:false
		checkbox chkCollect "Collect textures into scene folder first" pos:[18,248] checked:false
		checkbox chkTex "Also rename texture FILES on disk" pos:[18,266] checked:false
		label lblTexWarn "(renames copies in maps, never the originals)" pos:[36,282]

		button btnRun "ANALYZE & RENAME" pos:[16,302] width:298 height:30

		label lblFoot "Cost is roughly 1-3 cents per asset." pos:[12,348]
		label lblBy "Made by Pixel Lab  -" pos:[12,368]
		hyperLink lnkMail "support@pixellabsoft.com" address:"mailto:support@pixellabsoft.com" pos:[114,368]

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

		-- collecting and renaming texture files are allowed only in "selected objects" mode
		-- renaming works on copies only, so it always collects first
		fn syncTexOption =
		(
			local sel = (rbScope.state == 1)
			chkTex.enabled = sel
			lblTexWarn.enabled = sel
			if not sel do ( chkCollect.checked = false; chkTex.checked = false )
			if chkTex.checked do chkCollect.checked = true
			chkCollect.enabled = sel and not chkTex.checked
		)

		on rbScope changed s do syncTexOption()
		on chkTex changed s do syncTexOption()

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
			-- texture files need the scene's folder: offer to save a new scene first
			local fileOps = chkCollect.checked or chkTex.checked
			if fileOps and maxFilePath == "" do
			(
				if (queryBox ("The scene is not saved yet, so it has no folder\n" + \
					"for the texture files.\n\nSave the scene first?") title:"Ivo Material Renamer") do
				(
					local f = getMaxSaveFileName()
					if f != undefined do saveMaxFile f
				)
				if maxFilePath == "" do
				(
					fileOps = false
					messageBox ("Texture files will be left alone this time.\n" + \
						"Material names will still be renamed.") title:"Ivo Material Renamer"
				)
			)
			if fileOps and chkTex.checked do
			(
				if not (queryBox ("You enabled RENAMING OF TEXTURE FILES.\n\n" + \
					"The textures are first copied into this scene's\n" + \
					"maps folder and only those copies are renamed.\n" + \
					"The original files are never touched, so other\n" + \
					"scenes that use them keep working.\n\n" + \
					"Continue?") title:"Rename texture files?") do return false
			)
			setINISetting iniFile "API" "Scope" (rbScope.state as string)
			setINISetting iniFile "API" "RenameTextures" (if fileOps and chkTex.checked then "1" else "0")
			setINISetting iniFile "API" "CollectTextures" (if fileOps and chkCollect.checked then "1" else "0")
			setINISetting iniFile "API" "RenameMaps" (if chkMaps.checked then "1" else "0")
			setINISetting iniFile "API" "ConvertBitmaps" (if chkConv.checked then "1" else "0")
			setINISetting iniFile "API" "Version" "1.4.1"   -- the .py checks it matches
			python.ExecuteFile pyFile
		)
	)
	createDialog IvoMatRenamerRoll
	-- the rollout above is local to this block; remember it globally so the
	-- next run can close this window instead of opening a second one
	::IvoMatRenamerRoll = IvoMatRenamerRoll
)
