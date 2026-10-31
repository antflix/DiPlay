package com.shilapi.xcertplay.glance

import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.shilapi.xcertplay.iap2.message.LaneGuidanceLane
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Independent best-effort Pi HUD publisher. All HTTP and JSON work stays off the
 * CarPlay/iAP2/UI threads; failures cannot interrupt the CarPlay session.
 *
 * This personal build targets the van's Raspberry Pi on the trusted LAN.
 */
object NavigationHudRelay {
    private const val TAG = "DiPlay-HudRelay"
    private const val ENDPOINT = "http://192.168.8.136:8765/navigation"
    private const val LANE_TTL_MS = 25_000L
    private val started = AtomicBoolean(false)
    private val latest = AtomicReference(CarPlayGlance.Snapshot())
    private data class LaneState(val items: List<LaneGuidanceLane>, val observedMs: Long)
    private val laneState = AtomicReference(LaneState(emptyList(), 0L))
    private val worker = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "diplay-pi-navigation").apply { isDaemon = true }
    }
    private var lastFailure: String? = null

    fun install(context: Context) {
        if (!started.compareAndSet(false, true)) return
        CarPlayGlance.hudListener = { next ->
            val previous = latest.getAndSet(next)
            // Do not carry lane advice into a new maneuver or route.
            if (!next.connected || next.maneuverType == null ||
                previous.maneuverType != next.maneuverType || previous.road != next.road) {
                laneState.set(LaneState(emptyList(), 0L))
            }
        }
        CarPlayGlance.hudLaneListener = { lanes ->
            laneState.set(LaneState(lanes.toList().take(12), SystemClock.elapsedRealtime()))
        }
        latest.set(CarPlayGlance.snapshot())
        worker.scheduleWithFixedDelay({
            try {
                // Route instructions expire even if the phone stops publishing; refresh
                // source state, rather than blindly repeating an obsolete cached snapshot.
                publish(CarPlayGlance.snapshot())
            } catch (error: Exception) {
                reportFailure(error)
            }
        }, 0, 2, TimeUnit.SECONDS)
        Log.i(TAG, "Pi HUD relay active")
    }

    private fun publish(state: CarPlayGlance.Snapshot) {
        val active = state.connected && state.maneuverType != null
        val observed = laneState.get()
        val lanes = JSONArray()
        if (active && observed.observedMs > 0 &&
            SystemClock.elapsedRealtime() - observed.observedMs < LANE_TTL_MS) {
            for (lane in observed.items) {
                lanes.put(JSONObject().apply {
                    put("index", lane.index)
                    put("status", lane.status)
                    put("recommended", lane.recommended)
                    put("angle", lane.angle)
                    put("highlightAngle", lane.highlightAngle ?: JSONObject.NULL)
                })
            }
        }
        val body = JSONObject().apply {
            put("connected", state.connected)
            put("navigationActive", active)
            put("maneuverType", if (active) state.maneuverType else JSONObject.NULL)
            put("drivingSide", state.drivingSide)
            put("distanceMeters", if (active) state.distanceMeters else JSONObject.NULL)
            put("road", if (active) state.road else "")
            put("arrivalEpochSeconds", if (active) state.arrivalEpochSeconds ?: JSONObject.NULL else JSONObject.NULL)
            put("remainingSeconds", if (active) state.remainingSeconds ?: JSONObject.NULL else JSONObject.NULL)
            put("remainingMeters", if (active) state.remainingMeters ?: JSONObject.NULL else JSONObject.NULL)
            put("lanes", lanes)
        }.toString()
        val connection = URL(ENDPOINT).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 1200
            connection.readTimeout = 1200
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            if (connection.responseCode !in 200..299)
                throw IllegalStateException("Pi HTTP " + connection.responseCode)
            if (lastFailure != null) {
                Log.i(TAG, "Pi HUD receiver reachable again")
                lastFailure = null
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun reportFailure(error: Exception) {
        val reason = error.javaClass.simpleName + ": " + (error.message ?: "")
        if (reason != lastFailure) {
            lastFailure = reason
            Log.w(TAG, "Pi HUD relay unavailable: " + reason)
        }
    }
}
