import express from "express";
import path from "path";
import { createServer as createViteServer } from "vite";
import { createClient } from "@supabase/supabase-js";
import * as ics from "ics";
import dotenv from "dotenv";

dotenv.config();

const SUPABASE_URL = process.env.SUPABASE_URL || "";
const SUPABASE_KEY = process.env.SUPABASE_KEY || "";
const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

async function startServer() {
  const app = express();
  const PORT = 3000;

  // --- API Routes ---

  // Health check
  app.get("/api/health", (req, res) => {
    res.json({ status: "ok" });
  });

  // iCal Feed Endpoint
  // Example: /api/calendar/Y1B,Y4B.ics
  app.get("/api/calendar/:classes.ics", async (req, res) => {
    try {
      const classesParam = req.params.classes.replace(".ics", "");
      const selectedClasses = classesParam.split(",").map(c => c.trim()).filter(c => c);

      if (selectedClasses.length === 0) {
        return res.status(400).send("No classes specified");
      }

      // Fetch events from Supabase
      const { data: events, error } = await supabase
        .from("events")
        .select("*")
        .neq("type", "SYSTEM_META")
        .eq("status", "approved");

      if (error) throw error;

      // Filter events
      const filteredEvents = (events || []).filter(e => {
        // If the user requested 'All', return everything
        if (selectedClasses.includes("All")) return true;
        
        let eventClasses: string[] = [];
        if (Array.isArray(e.classes)) {
          eventClasses = e.classes;
        } else if (typeof e.classes === "string") {
          try {
            eventClasses = JSON.parse(e.classes);
          } catch (err) {
            eventClasses = [e.classes];
          }
        }

        // If the event is marked for 'All', it matches any selection
        if (eventClasses.some(c => c.toLowerCase() === "all")) return true;
        
        // Otherwise, check for specific class matches (case-insensitive)
        return eventClasses.some((c: string) => 
          selectedClasses.some(sc => sc.toLowerCase() === c.toLowerCase())
        );
      });

      // Map to ics format
      const icsEvents: ics.EventAttributes[] = filteredEvents.map(e => {
        const date = new Date(e.event_date);
        const start: ics.DateArray = [
            date.getFullYear(),
            date.getMonth() + 1,
            date.getDate()
        ];
        
        let prefix = "BWA";
        
        // Re-parse classes for prefix logic
        let eventClasses: string[] = [];
        if (Array.isArray(e.classes)) {
          eventClasses = e.classes;
        } else if (typeof e.classes === "string") {
          try {
            eventClasses = JSON.parse(e.classes);
          } catch (err) {
            eventClasses = [e.classes];
          }
        }

        if (!eventClasses.some(c => c.toLowerCase() === "all")) {
            // Find which of the user's selected classes this event belongs to
            const relevantClass = eventClasses.find((c: string) => 
               selectedClasses.some(sc => sc.toLowerCase() === c.toLowerCase())
            );
            if (relevantClass) {
                prefix = `BWA ${relevantClass}`;
            }
        }

        const duration: ics.DurationObject = { days: 1 };
        
        // Handle end date if it exists
        if (e.event_date_end) {
            const endDate = new Date(e.event_date_end);
            const diffTime = Math.abs(endDate.getTime() - date.getTime());
            const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24)) + 1;
            duration.days = diffDays;
        }

        return {
          uid: `${e.id}@bwa-calendar.io`,
          start,
          duration,
          title: `${prefix}: ${e.title}`,
          description: (e.summary || "") + (e.full_details ? "\n\n" + e.full_details : ""),
          categories: [e.type || "Event"],
          status: "CONFIRMED",
          busyStatus: "BUSY",
          productId: "BWA School Calendar",
          calName: `BWA Calendar - ${classesParam}`
        };
      });

      if (icsEvents.length === 0) {
        // Google often rejects empty calendars. Add a placeholder event.
        icsEvents.push({
          uid: 'placeholder@bwa-calendar.io',
          start: [new Date().getFullYear(), new Date().getMonth() + 1, new Date().getDate(), 9, 0],
          duration: { hours: 0, minutes: 1 },
          title: 'BWA Calendar Active',
          description: 'Your subscription to the BWA School Calendar is active.',
          status: 'CONFIRMED',
          busyStatus: 'FREE'
        });
      }

      const { error: icsError, value } = ics.createEvents(icsEvents);
      if (icsError) throw icsError;

      // Add standard headers for subscription refresh and name with CRLF line endings
      const calName = `BWA Calendar - ${classesParam}`;
      const headers = [
        `X-WR-CALNAME:${calName}`,
        `NAME:${calName}`,
        `X-WR-CALDESC:BWA School Calendar - ${classesParam}`,
        'X-WR-TIMEZONE:Europe/London',
        'X-PUBLISHED-TTL:PT1H',
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H'
      ].join('\r\n');

      // Use a more descriptive PRODID and place headers after VERSION:2.0
      let finalValue = value.replace('PRODID:-//adamgibbons//ics//EN', 'PRODID:-//BWA//Calendar//EN');
      finalValue = finalValue.replace('VERSION:2.0', `VERSION:2.0\r\n${headers}`);
      
      // Ensure all line endings are CRLF (\r\n) which is required by the iCal spec
      finalValue = finalValue.replace(/\r?\n/g, '\r\n');

      res.setHeader("Content-Type", "text/calendar; charset=utf-8");
      res.setHeader("Cache-Control", "public, max-age=3600"); 
      res.send(finalValue);
    } catch (err) {
      console.error("iCal error:", err);
      res.status(500).send("Error generating calendar");
    }
  });

  // --- Vite Middleware ---
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
