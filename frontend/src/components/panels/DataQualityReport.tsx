'use client';

import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { ResizableDrawer } from './ResizableDrawer';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { PieChart, Pie, Cell } from 'recharts';

const GOLD = '#D4A843';
const GREEN = '#4CAF50';
const RED = '#F44336';
const GRAY = '#3A3A3E';
const DRAWER_WIDTH = 500;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface QualityIssue {
  table: string;
  column?: string;
  detail: string;
}

interface QualityCheckSection {
  title: string;
  issues: QualityIssue[];
}

interface TableQualitySummary {
  tableName: string;
  rowCount: number;
  issueCount: number;
  score: number;
}

interface QualityReport {
  overallScore: number;
  totalTables: number;
  passingTables: number;
  checks: QualityCheckSection[];
  tableSummaries: TableQualitySummary[];
}

interface DataQualityReportProps {
  open: boolean;
  onClose: () => void;
  report: QualityReport | null;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function getScoreColor(score: number): string {
  if (score >= 70) return GREEN;
  if (score >= 40) return GOLD;
  return RED;
}

/* ------------------------------------------------------------------ */
/*  Donut chart center label (rendered as overlay)                     */
/* ------------------------------------------------------------------ */

interface CenterLabelProps {
  score: number;
  color: string;
}

function CenterLabel({ score, color }: CenterLabelProps): React.ReactNode {
  return (
    <Box
      sx={{
        position: 'absolute',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        textAlign: 'center',
      }}
    >
      <Typography
        sx={{
          fontSize: 36,
          fontWeight: 700,
          color: color,
          lineHeight: 1,
        }}
      >
        {score}
      </Typography>
      <Typography
        variant="caption"
        sx={{ color: 'text.secondary', fontSize: 11 }}
      >
        / 100
      </Typography>
    </Box>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function DataQualityReport({
  open,
  onClose,
  report,
}: DataQualityReportProps): React.ReactNode {
  const score = report?.overallScore ?? 0;
  const color = getScoreColor(score);

  const donutData = [
    { name: 'score', value: score },
    { name: 'remaining', value: 100 - score },
  ];

  return (
    <ResizableDrawer
      defaultWidth={DRAWER_WIDTH}
      open={open}
      onClose={onClose}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 2.5,
            py: 2,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Typography variant="h6" fontWeight={700}>
            Data Quality Report
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Scrollable content */}
        <Box sx={{ flex: 1, overflow: 'auto', px: 2.5, py: 2 }}>
          {report === null ? (
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 4 }}>
              No report data available.
            </Typography>
          ) : (
            <>
              {/* Donut chart with center score */}
              <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
                <Box sx={{ position: 'relative', width: 180, height: 180 }}>
                  <PieChart width={180} height={180}>
                    <Pie
                      data={donutData}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={80}
                      startAngle={90}
                      endAngle={-270}
                      dataKey="value"
                      stroke="none"
                    >
                      <Cell fill={color} />
                      <Cell fill={GRAY} />
                    </Pie>
                  </PieChart>
                  <CenterLabel score={score} color={color} />
                </Box>
              </Box>

              {/* Summary text */}
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ textAlign: 'center', mb: 3 }}
              >
                {report.passingTables} of {report.totalTables} tables meet quality threshold
              </Typography>

              {/* Collapsible check sections */}
              {report.checks?.map((section) => (
                <Accordion
                  key={section.title}
                  disableGutters
                  elevation={0}
                  sx={{
                    bgcolor: 'background.paper',
                    border: 1,
                    borderColor: 'divider',
                    '&:before': { display: 'none' },
                    mb: 1,
                    borderRadius: '8px !important',
                    overflow: 'hidden',
                  }}
                >
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                      <Typography variant="body2" fontWeight={600}>
                        {section.title}
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{
                          ml: 'auto',
                          mr: 1,
                          color: section.issues.length > 0 ? GOLD : GREEN,
                          fontWeight: 600,
                        }}
                      >
                        {section.issues.length} issue{section.issues.length !== 1 ? 's' : ''}
                      </Typography>
                    </Box>
                  </AccordionSummary>
                  <AccordionDetails sx={{ pt: 0 }}>
                    {section.issues.length === 0 ? (
                      <Typography variant="caption" color="text.secondary">
                        No issues detected.
                      </Typography>
                    ) : (
                      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                        {section.issues.map((issue, idx) => (
                          <Box
                            key={`${issue.table}-${issue.column ?? ''}-${idx}`}
                            sx={{ pl: 1, borderLeft: 2, borderColor: GOLD }}
                          >
                            <Typography variant="caption" fontWeight={600}>
                              {issue.table}
                              {issue.column ? ` . ${issue.column}` : ''}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ display: 'block' }}
                            >
                              {issue.detail}
                            </Typography>
                          </Box>
                        ))}
                      </Box>
                    )}
                  </AccordionDetails>
                </Accordion>
              ))}

              {/* Per-table summary */}
              {report.tableSummaries && report.tableSummaries.length > 0 && (
                <Box sx={{ mt: 3 }}>
                  <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
                    Per-Table Summary
                  </Typography>
                  <TableContainer
                    sx={{
                      border: 1,
                      borderColor: 'divider',
                      borderRadius: 1,
                      overflow: 'hidden',
                    }}
                  >
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell sx={{ fontWeight: 700 }}>Table Name</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 700 }}>Rows</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 700 }}>Issues</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 700 }}>Score</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {report.tableSummaries.map((ts) => (
                          <TableRow key={ts.tableName}>
                            <TableCell>
                              <Typography variant="body2">{ts.tableName}</Typography>
                            </TableCell>
                            <TableCell align="right">
                              <Typography variant="body2">
                                {ts.rowCount.toLocaleString()}
                              </Typography>
                            </TableCell>
                            <TableCell align="right">
                              <Typography variant="body2">{ts.issueCount}</Typography>
                            </TableCell>
                            <TableCell align="right">
                              <Typography
                                variant="body2"
                                fontWeight={600}
                                sx={{ color: getScoreColor(ts.score) }}
                              >
                                {ts.score}%
                              </Typography>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Box>
              )}
            </>
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type {
  QualityReport,
  QualityIssue,
  QualityCheckSection,
  TableQualitySummary,
  DataQualityReportProps,
};
