# The daily core backup leaves the host encrypted; the first key was rotated the same morning

- **Why:** scorecard Q1 (`docs/proposals/SYSTEM_SCORECARD_V0.1.md`). The core archive carries `.env` and the
  assistant's credentials and is pulled to the Mac daily; until today it was a plaintext `tar.gz`. Encryption
  to an age public key was decided 2026-09-29 and built in #1024, which waited on the key.
- **Done 2026-10-06:** Thomas generated the key pair on the Mac (Homebrew, `age-keygen`); the host got the
  public key in `/root/backups/age-recipients.txt`, #1024 merged, both scripts installed from main (the
  previous copies kept as `.pre-age-20261006`), one manual core run. The Mac installed the new pull script;
  its launchd job was already registered; the pull decrypted the new archive and listed it: `OK`.
- **The first key was exposed and replaced.** A screenshot taken while checking the key file showed the
  private key in the conversation. Only one test archive had been encrypted to it. A new pair was generated
  (shown with `age-keygen -y`, copied with `pbcopy`, never printed), the recipients file replaced, and the
  test archive deleted on both sides — before the first scheduled encrypted run at 07:45Z.
- **Left:** the seven plaintext core archives on the host age out once seven encrypted ones exist; plaintext
  copies already on the Mac are Thomas's to delete.
