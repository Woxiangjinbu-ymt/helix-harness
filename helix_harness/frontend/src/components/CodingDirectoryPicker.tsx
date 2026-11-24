import { useEffect, useRef, useState } from "react";
import { ArrowUp, ChevronRight, Folder, X } from "lucide-react";
import { useI18n } from "../i18n/context";
import { api } from "../lib/api";

type Listing = { directory: string; parent: string; folders: string[] };

export function CodingDirectoryPicker({
  onOpen,
  onClose,
}: {
  onOpen: (directory: string) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const c = t.coding;
  const dialog = useRef<HTMLDialogElement>(null);
  const [location, browse] = useState({ directory: "" });
  const [listing, setListing] = useState<Listing>();
  const [path, setPath] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState(false);
  const busy = useRef(false);
  const last = localStorage.getItem("coding.lastDirectory");
  const selected = path ?? listing?.directory ?? "";

  useEffect(() => {
    const node = dialog.current!;
    node.showModal();
    return () => node.close();
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    api<Listing>("coding/directories", location)
      .then((result) => {
        if (live) setListing(result);
      })
      .catch((e) => {
        if (live) {
          setListing(undefined);
          setError(e.message);
        }
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [location]);

  function navigate(directory: string) {
    setPath(directory);
    browse({ directory });
  }
  async function open() {
    if (busy.current || !selected.trim()) return;
    busy.current = true;
    setOpening(true);
    setError("");
    try {
      await onOpen(selected.trim());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      busy.current = false;
      setOpening(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="coding-picker"
      aria-labelledby="folder-heading"
      onCancel={(e) => {
        e.preventDefault();
        if (!opening) onClose();
      }}
    >
      <header>
        <h2 id="folder-heading">{c.choose}</h2>
        <button
          type="button"
          aria-label={t.controls.close}
          disabled={opening}
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </header>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void open();
        }}
      >
        <label htmlFor="working-path">{c.directory}</label>
        <div className="coding-picker-path">
          <input
            id="working-path"
            name="directory"
            autoComplete="off"
            autoFocus
            value={selected}
            disabled={opening}
            onChange={(e) => {
              setPath(e.target.value);
              setError("");
            }}
          />
          <button
            type="button"
            disabled={opening || !selected.trim()}
            onClick={() => navigate(selected.trim())}
          >
            {c.browse}
          </button>
        </div>
        {error && (
          <p className="coding-inline-error" role="alert">
            {error}
          </p>
        )}
        {last && last !== listing?.directory && (
          <button
            className="coding-last-folder"
            type="button"
            disabled={opening}
            title={last}
            onClick={() => navigate(last)}
          >
            <Folder size={14} />
            {c.lastFolder} · {last.split("/").pop() || "/"}
          </button>
        )}
        <div className="coding-folder-list" aria-busy={loading}>
          {loading ? (
            <p role="status">{t.controls.loading}</p>
          ) : (
            listing && (
              <>
                {listing.parent !== listing.directory && (
                  <button
                    type="button"
                    disabled={opening}
                    onClick={() => navigate(listing.parent)}
                  >
                    <ArrowUp size={15} />
                    {c.parentFolder}
                  </button>
                )}
                {listing.folders.map((name) => (
                  <button
                    key={name}
                    type="button"
                    disabled={opening}
                    onClick={() =>
                      navigate(
                        (listing.directory === "/" ? "" : listing.directory) +
                          "/" +
                          name,
                      )
                    }
                  >
                    <Folder size={16} />
                    <span>{name}</span>
                    <ChevronRight size={14} />
                  </button>
                ))}
                {!listing.folders.length && <p>{c.noFolders}</p>}
              </>
            )
          )}
        </div>
        <footer>
          <button
            type="submit"
            className="coding-primary"
            disabled={opening || !selected.trim()}
          >
            {opening ? c.opening : c.open}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
