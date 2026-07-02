from faster_whisper import WhisperModel

print("==================================================")
print(" Downloading Whisper large-v3-turbo...")
print(" You will see real-time progress bars below.")
print("==================================================")

# We use CPU and int8 here just to trigger the download safely 
# without worrying about GPU memory limits for this specific script.
model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")

print("\n==================================================")
print(" SUCCESS! Download is 100% complete.")
print(" You can now close this and run gemini_dravixa.py!")
print("==================================================")

